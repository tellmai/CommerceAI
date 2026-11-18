"""Synthetic protocol fixtures; no vendor account, live prices or billable calls."""

from datetime import timedelta

import httpx
import pytest

from app.db import Job, Session, now
from app.domain import fingerprint
from app.errors import BusinessError
from app.market_analysis import analyze
from app.market_provider import OneBound, normalize_item, safe_url
from app.market_schemas import CompareInput
from app.market_service import demo_items


def parameters(run):
    return {
        "expected_version": run["version"],
        "supply_id": next(i["id"] for i in run["data"]["items"] if i["platform"] == "1688"),
        "retail_id": next(i["id"] for i in run["data"]["items"] if i["platform"] == "taobao"),
        "purchase_quantity": 100,
        "supply_pack": 2,
        "retail_pack": 2,
        "min_order": 1,
        "match_confirmed": True,
        "verification_reason": "测试核验同包装、材质、尺寸和人民币报价",
        "inbound_shipping": "10",
        "outbound_shipping": "3",
        "packaging": ".2",
        "fees": ".5",
        "advertising": "1",
        "refund_rate": 0.05,
        "refund_loss": "5",
        "ad_budget": "100",
    }


def complete(client, mode="DEMO"):
    response = client.post("/api/v1/market/runs", json={"query": "牙刷", "per_platform": 1, "mode": mode})
    assert response.status_code == 202, response.text
    value = response.json()
    from app.worker import run_once

    for _ in range(10):
        status = client.get("/api/v1/jobs/" + value["job_id"]).json()["status"]
        if status not in {"QUEUED", "RUNNING"}:
            break
        run_once()
    return client.get("/api/v1/market/runs/" + value["run"]["id"]).json()


def test_unit_cost_tiers_unknowns_and_frozen_inputs():
    supply = demo_items("1688", "牙刷", 1)[0]
    retail = demo_items("taobao", "牙刷", 1)[0]
    fake_run = {"version": 1, "data": {"items": [supply, retail]}}
    body = CompareInput.model_validate(parameters(fake_run))
    result = analyze(supply, retail, body, "LIVE")
    assert result["normal"]["contribution"] == "5.6850"
    assert result["stress"]["contribution"] == "3.4380"
    assert result["initial_cash"] == "290.0000"
    assert result["recommendation"] == "REVIEW_SAMPLE"
    assert result["sticker_spread"] == "11.1000"
    body.inbound_shipping = None
    missing = analyze(supply, retail, body, "LIVE")
    assert missing["normal"] is None and missing["procurement_cost_per_sale_pack"] is None
    assert missing["recommendation"] == "NEEDS_VERIFICATION"
    body.inbound_shipping = 0
    assert analyze(supply, retail, body, "LIVE")["normal"] is not None


def test_moq_currency_specification_and_stale_quotes():
    supply, retail = demo_items("1688", "牙刷", 1)[0], demo_items("taobao", "牙刷", 1)[0]
    body = CompareInput.model_validate(parameters({"version": 1, "data": {"items": [supply, retail]}}))
    body.retail_pack = 1
    with pytest.raises(BusinessError, match="规格"):
        analyze(supply, retail, body, "LIVE")
    body.retail_pack, body.min_order = 2, 200
    assert analyze(supply, retail, body, "LIVE")["recommendation"] == "NOT_RECOMMENDED"
    body.min_order = 1
    supply["observed_at"] = (now() - timedelta(days=2)).isoformat()
    assert "报价采集已超过24小时，请重新查询" in analyze(supply, retail, body, "LIVE")["pending"]
    retail["currency"] = "USD"
    assert analyze(supply, retail, body, "LIVE")["recommendation"] == "NOT_RECOMMENDED"


@pytest.mark.parametrize("platform", ["1688", "taobao", "jd", "pinduoduo"])
def test_normalization_never_guesses_pack_or_shipping(platform):
    value = normalize_item(platform, {"num_iid": "123", "title": "牙刷2支装", "price": "0"})
    assert value["pack_count"] is None
    assert value["shipping"] is None
    assert value["price"] == "0.0000"
    assert safe_url("javascript:alert(1)", platform) == ""
    assert safe_url("https://127.0.0.1/secret", platform) == ""
    assert safe_url("https://attacker.example/", platform) == ""


def test_jd_duplicate_variant_prices_and_pdd_conditions():
    raw = {
        "num_iid": "123",
        "title": "测试",
        "price": "10",
        "skus": {
            "sku": [
                {"sku_id": "123", "price": "10"},
                {"sku_id": "456", "price": "10"},
            ]
        },
    }
    item = normalize_item("jd", raw, detail=True, requested_id="123")
    assert item["variants"][0]["price"] == "10.0000"
    assert item["variants"][1]["price"] is None
    item = normalize_item("pinduoduo", {**raw, "minGroupPrice": "8", "promotion_price": "7"})
    assert item["price"] == "10.0000" and any("拼团" in v for v in item["warnings"])


def test_provider_errors_do_not_expose_credentials(caplog):
    secret = "synthetic-secret-for-test-only"

    def rejected(request):
        assert request.url.host == "api-gw.onebound.cn"
        assert request.url.params["cache"] == "no"
        return httpx.Response(200, json={"error_code": "4005", "reason": secret, "error": "bad " + secret})

    provider = OneBound({"api_key": secret}, transport=httpx.MockTransport(rejected))
    try:
        with pytest.raises(BusinessError) as failure:
            provider.search("1688", "牙刷", 1, 1)
        assert secret not in str(failure.value)
        assert secret not in caplog.text
    finally:
        provider.close()


@pytest.fixture
def market_config(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_missing_configuration_free_mode_and_secrets(client, market_config):
    client.headers["X-Workspace"] = "live"
    response = client.post("/api/v1/market/runs", json={"query": "牙刷", "mode": "LIVE"})
    assert response.status_code == 409 and response.json()["code"] == "PLATFORM_NOT_CONFIGURED"
    assert client.get("/api/v1/market/runs").json()["total"] == 0
    assert client.post("/api/v1/market/runs", json={"query": "牙刷", "mode": "DEMO"}).status_code == 409
    key, secret = "synthetic-account-key", "synthetic-account-secret"
    response = client.put(
        "/api/v1/market/connection",
        json={
            "enabled": True,
            "api_key": key,
            "api_secret": secret,
            "permission_note": "仅在隔离测试中模拟授权",
        },
    )
    assert response.status_code == 200
    assert key not in response.text and secret not in response.text
    assert key not in client.get("/api/v1/market/connection").text
    assert key not in client.get("/api/v1/audit").text
    private = market_config / "private/market-connection.json"
    assert private.stat().st_mode & 0o777 == 0o600
    response = client.put(
        "/api/v1/market/connection", json={"enabled": False, "api_key": "", "api_secret": ""}
    )
    assert response.status_code == 200 and response.json()["configured"] is False


def test_demo_end_to_end_report_history_export_candidate_and_isolation(client, market_config):
    run = complete(client)
    assert run["job"]["status"] == "SUCCEEDED" and len(run["data"]["items"]) == 4
    assert run["data"]["calls_reserved"] == 0
    body = parameters(run)
    response = client.post(f"/api/v1/market/runs/{run['id']}/compare", json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    assert first["data"]["result"]["recommendation"] == "DEMO_ONLY"
    assert first["data"]["result"]["normal"]["contribution"] == "5.6850"
    signature = fingerprint(first["data"]["result"])
    body["sale_price"] = "0"
    later = client.post(f"/api/v1/market/runs/{run['id']}/compare", json=body)
    assert later.status_code == 201 and later.json()["id"] != first["id"]
    assert (
        fingerprint(client.get("/api/v1/market/reports/" + first["id"]).json()["data"]["result"]) == signature
    )
    exported = client.get("/api/v1/market/reports/" + first["id"] + "/export")
    assert exported.status_code == 200 and "DEMO" in exported.text
    promoted = client.post(
        "/api/v1/market/reports/" + first["id"] + "/candidate",
        json={"name": "牙刷研究样例", "category": "口腔护理"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["data"]["state"] == "DISCOVERED"
    client.headers["X-Workspace"] = "live"
    assert client.get("/api/v1/market/runs/" + run["id"]).status_code == 404
    assert client.get("/api/v1/market/reports/" + first["id"]).status_code == 404


def test_partial_live_protocol_results_and_call_limit(client, market_config, monkeypatch):
    from app import market_service

    original = OneBound

    def response(request):
        platform, method = request.url.path.strip("/").split("/")
        if platform == "jd":
            return httpx.Response(200, json={"error_code": "4005", "error": "no access"})
        raw = {
            "num_iid": "123",
            "title": "合成协议测试牙刷",
            "price": "2" if platform == "1688" else "12.9",
            "pack_count": 2,
            "min_num": 1,
            "currency": "CNY",
            "attributes": {"material": "PBT", "size": "180mm"},
        }
        return httpx.Response(
            200,
            json={"error_code": "0000", "items": {"item": [raw]}}
            if method == "item_search"
            else {"item": raw},
        )

    monkeypatch.setattr(
        market_service,
        "OneBound",
        lambda connection: original(connection, transport=httpx.MockTransport(response)),
    )
    client.put(
        "/api/v1/market/connection",
        json={"enabled": True, "api_key": "synthetic-key", "permission_note": "合成协议测试"},
    )
    client.headers["X-Workspace"] = "live"
    run = complete(client, "LIVE")
    assert run["data"]["status"] == "PARTIAL"
    assert {v["platform"] for v in run["data"]["items"]} == {"1688", "taobao", "pinduoduo"}
    assert run["data"]["errors"][0]["platform"] == "jd"
    assert run["data"]["calls_reserved"] == 7 <= run["data"]["call_limit"]
    with Session.begin() as db:
        job = db.get(Job, run["job"]["id"])
        assert job.status == "SUCCEEDED"


def test_confirmed_mismatch_and_version_conflict_are_rejected(client, market_config):
    run = complete(client)
    body = parameters(run)
    body["retail_pack"] = 1
    url = f"/api/v1/market/runs/{run['id']}/compare"
    assert client.post(url, json=body).status_code == 409
    body["retail_pack"], body["expected_version"] = 2, 999999
    assert client.post(url, json=body).json()["code"] == "VERSION_CONFLICT"
    client.headers["X-Workspace"] = "public"
    assert client.post("/api/v1/market/runs", json={"query": "牙刷", "mode": "LIVE"}).status_code == 409


def test_cancel_updates_query_and_blocks_report(client, market_config):
    result = client.post("/api/v1/market/runs", json={"query": "牙刷", "mode": "DEMO"}).json()
    assert client.post("/api/v1/jobs/" + result["job_id"] + "/cancel").status_code == 200
    run = client.get("/api/v1/market/runs/" + result["run"]["id"]).json()
    assert run["data"]["status"] == "CANCELLED" and run["job"]["status"] == "CANCELLED"


def test_manual_quote_to_research_with_connection_disabled(client, market_config):
    shared = {
        "title": "人工流程合成牙刷",
        "pack_count": 2,
        "material": "PBT",
        "size": "180mm",
        "evidence_note": "测试凭据：两支装指定型号",
    }
    body = {
        "query": "牙刷",
        "quotes": [
            {
                **shared,
                "platform": "1688",
                "url": "https://detail.1688.com/offer/123.html?token=drop-me",
                "price": "2",
                "min_order": 1,
            },
            {
                **shared,
                "platform": "taobao",
                "url": "https://item.taobao.com/item.htm?id=321",
                "price": "12.9",
                "shipping": "0",
            },
        ],
    }
    url = "/api/v1/market/runs/manual"
    assert client.post(url, json=body).status_code == 409
    client.headers["X-Workspace"] = "live"
    assert not client.get("/api/v1/market/connection").json()["configured"]
    bad_link = {
        **body,
        "quotes": [body["quotes"][0], {**body["quotes"][1], "url": "https://taobao.com.example/"}],
    }
    assert client.post(url, json=bad_link).json()["code"] == "INVALID_PLATFORM_URL"
    response = client.post(url, json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert "drop-me" not in response.text and run["data"]["request"]["mode"] == "MANUAL"
    assert run["data"]["calls_reserved"] == 0 and run["data"]["items"][1]["shipping"] == "0.0000"
    run_url = "/api/v1/market/runs/" + run["id"]
    assert client.get(run_url).json()["job"] is None
    result = client.post(run_url + "/compare", json=parameters(run))
    assert result.status_code == 201, result.text
    report = result.json()
    assert report["data"]["mode"] == "MANUAL"
    assert report["data"]["result"]["recommendation"] == "REVIEW_SAMPLE"
    assert report["data"]["result"]["normal"]["contribution"] == "5.4950"
    assert report["data"]["result"]["sticker_spread"] == "10.9000"
    result = client.post(
        f"/api/v1/market/reports/{report['id']}/candidate",
        json={"name": "人工记录的牙刷", "category": "日用测试"},
    )
    assert result.status_code == 200, result.text
    client.headers["X-Workspace"] = "public"
    assert client.get(run_url).status_code == 404


def test_repacking_does_not_confirm_same_sku_and_dropship_needs_own_quote():
    supply = demo_items("1688", "牙刷", 1)[0]
    retail = demo_items("taobao", "牙刷", 1)[0]
    body = CompareInput.model_validate(parameters({"version": 1, "data": {"items": [supply, retail]}}))
    body.procurement_mode, body.dropship_verified, body.purchase_quantity = "DROPSHIP", True, 1
    assert analyze(supply, retail, body, "MANUAL")["normal"] is None
    body.procurement_mode = "STOCK"
    retail["pack_count"] = body.retail_pack = 6
    body.allow_repack = True
    with pytest.raises(BusinessError, match="不能确认同款"):
        analyze(supply, retail, body, "MANUAL")
    body.match_confirmed = False
    result = analyze(supply, retail, body, "MANUAL")
    assert result["match_state"] == "UNVERIFIED" and result["recommendation"] == "NOT_RECOMMENDED"
