from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import io


def records(client, kind):
    response = client.get("/api/v1/" + kind + "?page_size=100")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_auth_and_csrf(client):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/candidates").status_code == 401
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/profit/calculate", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf


def test_three_spaces_and_cross_space_reference(client, evidence):
    product = records(client, "products")[0]
    client.headers["X-Workspace"] = "live"
    assert client.get("/api/v1/products").json()["total"] == 0
    assert client.get("/api/v1/products/" + product["id"]).status_code == 404
    response = client.post(
        "/api/v1/skus",
        json={
            "product_id": product["id"],
            "name": "cross",
            "spec_key": "cross",
            "quantity": 1,
            "attributes": {},
        },
    )
    assert response.status_code == 404
    assert client.get("/api/v1/skus").json()["total"] == 0


def test_scoring_replay_and_independent_risk(client):
    candidate = next(c for c in records(client, "candidates") if "已阻断" in c["data"]["note"])
    detail = client.get(f"/api/v1/candidates/{candidate['id']}/research").json()
    assert detail["requirements"]["gate"] == "BLOCKED"
    score = detail["score"]
    assert score["data"]["gate"] == "BLOCKED"
    assert score["data"]["grade"] is None
    replay = client.get(f"/api/v1/scores/{score['id']}/replay").json()
    assert replay["result"]["factors"] == score["data"]["factors"]
    assert replay["input_hash"] == score["data"]["input_hash"]


def test_zero_shipping_and_procurement_exclusion(client):
    candidate = records(client, "candidates")[0]
    result = client.get(f"/api/v1/skus/{candidate['data']['sku_id']}/comparison").json()
    assert result["statistics"]["count"] == 5
    assert result["statistics"]["median"] == "49.9000"
    assert any("批发" in row["reason"] for row in result["excluded"])


def test_match_hard_conflict(client, evidence):
    ev, _ = evidence
    sku = records(client, "skus")[0]
    listing = client.post(
        "/api/v1/listings",
        json={
            "platform": "TEST",
            "external_id": ev["id"],
            "external_sku_id": "one",
            "title": "相似但不同件数",
            "quantity": 1,
            "attributes": sku["data"]["attributes"],
        },
    ).json()
    match = client.post(
        "/api/v1/matches",
        json={"listing_id": listing["id"], "sku_id": sku["id"], "evidence_id": ev["id"], "reason": "测试"},
    ).json()
    result = client.post(
        f"/api/v1/matches/{match['id']}/review",
        json={
            "state": "MATCHED",
            "evidence_id": ev["id"],
            "reason": "审核",
            "expected_version": match["version"],
        },
    )
    assert result.status_code == 409
    assert "quantity" in result.json()["details"]["conflicts"]


def test_buy_sample_without_previous_sample_and_concurrency(client):
    candidate = next(c for c in records(client, "candidates") if c["data"]["note"] == "等待购买样品")
    body = {
        "target_state": "READY_SAMPLE",
        "expected_version": candidate["version"],
        "reason": "测试并发人工买样",
        "evidence_ids": [],
    }
    with ThreadPoolExecutor(2) as pool:
        statuses = list(
            pool.map(
                lambda _: (
                    client.post(f"/api/v1/candidates/{candidate['id']}/transition", json=body).status_code
                ),
                range(2),
            )
        )
    assert sorted(statuses) == [200, 409]
    updated = client.get("/api/v1/candidates/" + candidate["id"]).json()
    result = client.post(
        f"/api/v1/candidates/{candidate['id']}/transition",
        json={**body, "expected_version": updated["version"], "target_state": "SAMPLE_PASSED"},
    )
    assert result.status_code == 409


def test_order_unknown_costs_and_revision(client, evidence):
    ev, source = evidence
    experiment = records(client, "experiments")[0]
    payload = {
        "source_id": source["id"],
        "evidence_id": ev["id"],
        "experiment_id": experiment["id"],
        "external_order_id": "private-test-order",
        "line_id": "1",
        "paid_at": datetime.now(UTC).isoformat(),
        "channel": "测试",
        "revenue": "39.9",
        "mature": True,
        "settled": True,
    }
    response = client.post("/api/v1/orders", json=payload)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["data"]["contribution"] is None
    assert row["data"]["external_order_id"].startswith("sha256:")
    assert client.post("/api/v1/orders", json=payload).status_code == 409
    payload.update(
        purchase="15",
        purchase_refund="0",
        shipping="4",
        packaging="1",
        net_fees="1",
        advertising="6",
        other_net_cost="0",
        customer_refund="5",
    )
    updated = client.put("/api/v1/orders/" + row["id"], json={"expected_version": 1, "payload": payload})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["contribution"] == "7.9"
    assert len(client.get("/api/v1/history/" + row["id"]).json()["items"]) == 2
    assert (
        client.put(
            "/api/v1/orders/" + row["id"], json={"expected_version": 1, "payload": payload}
        ).status_code
        == 409
    )


def test_import_preview_partial_success_and_idempotence(client, evidence):
    ev, source = evidence
    stamp = datetime.now(UTC) - timedelta(minutes=1)
    end = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    csv_text = "keyword,category,search_index,listing_count,conversion\n有效测试词,测试类,1000,100,0.08\n错误比例,测试类,1000,100,8\n"
    result = client.post(
        "/api/v1/imports/preview",
        data={"source_id": source["id"], "kind": "keywords"},
        files={"file": ("test.csv", csv_text.encode(), "text/csv")},
    )
    assert result.status_code == 200, result.text
    preview = result.json()
    assert len(preview["preview"]["rows"]) == 2
    body = {
        "mapping": {x: x for x in ["keyword", "category", "search_index", "listing_count", "conversion"]},
        "defaults": {
            "window_start": (end - timedelta(days=7)).isoformat(),
            "window_end": end.isoformat(),
            "observed_at": stamp.isoformat(),
            "metric_definition": "test",
        },
        "timezone": "Asia/Shanghai",
        "percent_unit": "fraction",
    }
    job = client.post(f"/api/v1/imports/{preview['id']}/confirm", json=body)
    assert job.status_code == 202, job.text
    repeated = client.post(f"/api/v1/imports/{preview['id']}/confirm", json=body)
    assert repeated.json()["id"] == job.json()["id"]
    from app.worker import run_once

    run_once()
    completed = client.get("/api/v1/jobs/" + job.json()["id"]).json()
    assert completed["status"] == "SUCCEEDED", completed
    assert completed["result"]["accepted"] == 1
    assert completed["result"]["rejected"] == 1
    errors = client.get(f"/api/v1/imports/{preview['id']}/errors")
    assert errors.status_code == 200 and "3,ROW_INVALID" in errors.text
    assert client.get("/api/v1/imports").json()["items"][0]["accepted"] == 1


def test_expired_source_blocks_import_and_filename_traversal(client, evidence):
    ev, source = evidence
    response = client.post(
        "/api/v1/imports/preview",
        data={"source_id": source["id"], "kind": "keywords"},
        files={"file": ("../bad.csv", b"a\n1\n", "text/csv")},
    )
    assert response.status_code == 422
    data = source["data"]
    data["enabled"] = False
    assert (
        client.put(
            "/api/v1/sources/" + source["id"], json={"expected_version": 1, "payload": data}
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/imports/preview",
        data={"source_id": source["id"], "kind": "keywords"},
        files={"file": ("good.csv", b"a\n1\n", "text/csv")},
    )
    assert response.json()["code"] == "SOURCE_NOT_AUTHORIZED"


def test_export_csv_and_xlsx(client, evidence):
    ev, source = evidence
    name = '=HYPERLINK("http://invalid")'
    created = client.post("/api/v1/products", json={"name": name, "category": "测试"})
    assert created.status_code == 201
    response = client.get("/api/v1/exports/products?format=csv")
    assert "'=HYPERLINK" in response.text
    xlsx = client.get("/api/v1/exports/products?format=xlsx")
    assert xlsx.status_code == 200
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(xlsx.content), read_only=True)
    assert all(c.data_type != "f" for row in workbook.active for c in row)


def test_strategy_version_immutable(client):
    strategy = records(client, "strategies")[0]
    assert (
        client.put(
            "/api/v1/strategies/" + strategy["id"], json={"expected_version": 1, "payload": strategy["data"]}
        ).status_code
        == 409
    )
    client.headers["X-Workspace"] = "live"
    payload = {**strategy["data"], "version": "must-not-use-demo"}
    assert client.post("/api/v1/strategies", json=payload).status_code == 422
