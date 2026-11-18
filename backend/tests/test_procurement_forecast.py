from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.opportunities import ForecastInput, forecast
from app.schemas import CONTRACTS
from test_workflows import create, move


def edit(client, resource, row, **changes):
    payload = {key: value for key, value in row["data"].items() if key in CONTRACTS[resource].model_fields}
    response = client.put(
        f"/api/v1/{resource}/{row['id']}",
        json={"expected_version": row["version"], "payload": {**payload, **changes}},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["STOCK", "DROPSHIP"])
def test_quote_to_reconciled_forecast_for_both_procurement_modes(client, mode):
    client.headers["X-Workspace"] = "live"
    name = "两支装牙刷 " + uuid4().hex[:10]
    raw = {
        "title": name,
        "variant_key": "两支装",
        "pack_count": 2,
        "material": "软毛PBT",
        "size": "18cm",
        "evidence_note": "隔离验收已选择两支装并确认报价条件",
    }
    quotes = [
        {
            **raw,
            "platform": "1688",
            "url": f"https://detail.1688.com/offer/{i}{uuid4().int}.html",
            "seller": f"验收供应商{i}",
            "price": "2.60",
            "min_order": 10 if mode == "STOCK" else 1,
            "price_tiers": ([{"min_quantity": 20, "price": "2.00"}] if mode == "STOCK" else []),
        }
        for i in range(2)
    ] + [
        {
            **raw,
            "platform": "taobao",
            "url": f"https://item.taobao.com/item.htm?id={uuid4().int}",
            "seller": f"验收零售卖家{i}",
            "price": price,
            "shipping_region": "杭州",
            "shipping": "0",
        }
        for i, price in enumerate(("12.90", "14.90"))
    ]
    run = create(client, "market/runs/manual", {"query": name, "quotes": quotes})
    items = run["data"]["items"]
    assert len(items) == 4
    duplicate = client.post(
        "/api/v1/market/runs/manual",
        json={"query": name, "quotes": quotes + [{**quotes[0], "url": quotes[0]["url"] + "?spm=tracking"}]},
    )
    assert duplicate.status_code == 422 and duplicate.json()["code"] == "DUPLICATE_QUOTE"
    reports, candidate = [], None
    for i in range(2):
        params = {
            "expected_version": run["version"],
            "supply_id": items[i]["id"],
            "retail_id": items[i + 2]["id"],
            "purchase_quantity": 20 if mode == "STOCK" else 1,
            "procurement_mode": mode,
            "match_confirmed": True,
            "verification_reason": "原链接与实物规格均已逐项核验",
            "supply_price": None if mode == "STOCK" else "2.00",
            "dropship_verified": mode == "DROPSHIP",
            "inbound_shipping": "10" if mode == "STOCK" else "2",
            "outbound_shipping": "3" if mode == "STOCK" else "0",
            "region": "杭州",
            "packaging": "0.2",
            "fees": "0.5",
            "fee_rate": 0.03,
            "advertising": "1",
            "refund_rate": 0.05,
            "refund_loss": "5",
            "ad_budget": "20",
        }
        report = create(client, f"market/runs/{run['id']}/compare", params)
        reports.append(report)
        response = client.post(
            f"/api/v1/market/reports/{report['id']}/candidate", json={"category": "日用验收", "name": name}
        )
        assert response.status_code == 200, response.text
        assert candidate is None or candidate["id"] == response.json()["id"]
        candidate = response.json()
        repeated = client.post(
            f"/api/v1/market/reports/{report['id']}/candidate", json={"category": "日用验收", "name": name}
        )
        assert repeated.json()["id"] == candidate["id"]

    assert candidate["data"]["procurement_mode"] == mode
    comparison = client.get(f"/api/v1/skus/{candidate['data']['sku_id']}/comparison?region=杭州").json()
    assert comparison["statistics"]["count"] == 2
    assert comparison["statistics"]["median"] == "13.9000"
    draft_baseline = client.get(
        "/api/v1/opportunities/baseline", params={"candidate_id": candidate["id"]}
    ).json()
    assert draft_baseline["region"] == "杭州" and draft_baseline["reference_count"] == 2
    assert draft_baseline["inputs"]["c"] is not None
    research = client.get(f"/api/v1/candidates/{candidate['id']}/research").json()
    drafts = {draft["report_id"]: draft for draft in research["market_drafts"]}
    main = drafts[reports[0]["id"]]
    assert main["offer"]["verified"] is False
    assert main["normal"]["c"] == ("2.5000" if mode == "STOCK" else "2.0000")
    assert main["normal"]["f"] == ("3.0000" if mode == "STOCK" else "2.0000")
    offers = []
    for i, report in enumerate(reports):
        supplier = create(client, "suppliers", {"name": name + f"供应商{i}", "external_id": uuid4().hex})
        offers.append(
            create(
                client,
                "offers",
                {
                    **drafts[report["id"]]["offer"],
                    "supplier_id": supplier["id"],
                    "lead_hours": 24,
                    "return_terms": "已核对验收商品退换条款",
                    "service_fee": "0",
                    "verified": True,
                },
            )
        )
    candidate = edit(
        client, "candidates", candidate, primary_offer_id=offers[0]["id"], backup_offer_id=offers[1]["id"]
    )
    if mode == "STOCK":
        original = offers[0]["data"]["batch_quantity"]
        offers[0] = edit(client, "offers", offers[0], batch_quantity=5)
        assert any(
            "起订量" in text
            for text in client.get(f"/api/v1/candidates/{candidate['id']}/research").json()["requirements"][
                "missing"
            ]
        )
        offers[0] = edit(client, "offers", offers[0], batch_quantity=original)
    for key in ("normal", "stress"):
        create(client, "profits", main[key])
    demand_source = create(
        client,
        "sources",
        {
            "name": name + "独立需求",
            "platform": "TEST",
            "permission_scope": ["keywords"],
            "permission_note": "合成需求仅用于隔离验收",
        },
    )
    stamp = datetime.now(ZoneInfo("Asia/Shanghai"))
    demand_proof = create(
        client,
        "evidence",
        {
            "source_id": demand_source["id"],
            "locator": "test://" + name,
            "observed_at": stamp.isoformat(),
        },
    )
    keyword = create(
        client,
        "keywords",
        {
            "source_id": demand_source["id"],
            "evidence_id": demand_proof["id"],
            "keyword": name,
            "category": "日用验收",
            "window_start": (stamp - timedelta(days=8)).isoformat(),
            "window_end": (stamp - timedelta(days=1)).isoformat(),
            "observed_at": stamp.isoformat(),
            "search_index": "3000",
        },
    )
    candidate = edit(client, "candidates", candidate, keyword_id=keyword["id"])
    ranked = lambda: client.get("/api/v1/opportunities", params={"search": name}).json()["items"][0]
    assert ranked()["level"] == "PRIORITY" and ranked()["next_action"] == "RESEARCH"
    candidate = move(client, candidate, "RESEARCHING")
    candidate = move(client, candidate, "READY_SAMPLE")
    for offer in offers:
        create(
            client,
            "samples",
            {
                "offer_id": offer["id"],
                "evidence_id": offer["data"]["evidence_id"],
                "result": "PASSED",
                "reason": "样品与到货验收通过",
            },
        )
    for risk in ("IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"):
        create(
            client,
            "risks",
            {
                "candidate_id": candidate["id"],
                "evidence_id": main["offer"]["evidence_id"],
                "risk_code": risk,
                "status": "PASS",
                "reason": "独立验收证据通过",
            },
        )
    candidate = move(client, move(client, candidate, "SAMPLE_PASSED"), "READY_TEST")
    reference = client.get("/api/v1/opportunities/baseline", params={"candidate_id": candidate["id"]}).json()
    assert reference["region"] == "杭州" and reference["reference_count"] == 2
    body = {
        **reference["inputs"],
        "price": reference["price"],
        "candidate_id": candidate["id"],
        "channel": "搜索",
        "target_margin": 0.2,
        "refund_amount_rate": 0.1,
        "traffic": {
            "days": 2,
            "daily_visits": 50,
            "low": 0.01,
            "base": 0.02,
            "high": 0.03,
            "denominator": "CLICKS",
        },
        "stock_units": 2 if mode == "STOCK" else None,
        "stock_batch_cash": "50" if mode == "STOCK" else None,
        "dropship_capacity_per_day": 1 if mode == "DROPSHIP" else None,
        "settlement_days": 1,
        "advertising_budget": "20",
        "refund_reserve": "5",
        "alternative_prices": [{"price": "15", "low": 0.01, "base": 0.02, "high": 0.03}],
    }
    plan = create(client, "forecasts", {"name": name + "方案", "inputs": body})
    previous = plan["data"]["result"]
    expected = previous["scenarios"][1]
    assert expected["orders"] == 2 and expected["net_revenue"] == "23.2200"
    assert expected["cash_required"] == ("75.0000" if mode == "STOCK" else "29.2000")
    assert previous["scenarios"][2]["shortfall"] == 1
    assert previous["alternative_scenarios"][0]["scenarios"][1]["paid_revenue"] == "30.0000"
    version_two = create(
        client,
        "forecasts",
        {
            "name": plan["data"]["name"],
            "inputs": {**body, "price": "13.90"},
            "parent_forecast_id": plan["id"],
        },
    )
    assert version_two["data"]["plan_version"] == 2
    assert client.get(f"/api/v1/forecasts/{plan['id']}/replay").json() == previous

    beijing = ZoneInfo("Asia/Shanghai")
    last = datetime.combine(datetime.now(beijing).date(), time.min, beijing)
    first = last - timedelta(days=2)
    experiment = create(
        client,
        "experiments",
        {
            "candidate_id": candidate["id"],
            "forecast_id": plan["id"],
            "start_at": first.isoformat(),
            "end_at": last.isoformat(),
            "budget": "20",
            "loss_limit": "10",
            "target_clicks": 25,
            "hypothesis": name + "测款",
        },
    )
    candidate = move(client, candidate, "TESTING")
    path = f"/api/v1/experiments/{experiment['id']}/summary"
    assert client.get(path).json()["remaining_budget"] is None
    saved_proof = client.get("/api/v1/evidence/" + main["offer"]["evidence_id"]).json()
    for i in range(2):
        create(
            client,
            "funnels",
            {
                "experiment_id": experiment["id"],
                "evidence_id": saved_proof["id"],
                "business_date": (first + timedelta(days=i)).date().isoformat(),
                "channel": "搜索",
                "clicks": 50,
                "paid_orders": 1 - i,
                "ad_spend": str(1 - i),
            },
        )
        create(
            client,
            "orders",
            {
                "experiment_id": experiment["id"],
                "evidence_id": saved_proof["id"],
                "source_id": saved_proof["data"]["source_id"],
                "external_order_id": name,
                "line_id": str(i),
                "paid_at": first.isoformat(),
                "channel": "搜索",
                "revenue": "6.45",
                "customer_refund": "1.2" if i == 0 else "0",
                "purchase": "0.50",
                "purchase_refund": "0",
                "shipping": "0",
                "packaging": "0",
                "net_fees": "0",
                "other_net_cost": "0",
                "mature": True,
                "settled": True,
            },
        )
    assert client.get(path).json()["forecast_comparison"]["state"] == "PENDING"
    assert client.post(f"/api/v1/experiments/{experiment['id']}/allocate-ads").status_code == 200
    summary = client.get(path).json()
    assert summary["mature_orders"] == 1 and summary["mature_contribution"] == "9.7000"
    review = summary["forecast_comparison"]
    assert review["state"] == "RECONCILED" and review["coverage"] == 1
    metrics = {row["key"]: row for row in review["metrics"]}
    assert metrics["orders"]["difference"] == -1
    assert metrics["net_revenue"]["actual"] == "11.7000" and metrics["refunds"]["actual"] == "1.2000"
    move(client, candidate, "SCALE")
    traffic = client.get("/api/v1/forecasts/traffic", params={"candidate_id": candidate["id"]}).json()
    assert traffic[0]["daily_clicks"] == 50 and traffic[0]["conversion"] == "0.0100"
    edit(client, "sources", demand_source, enabled=False)
    assert ranked()["level"] == "VERIFY" and "需求证据不足" in ranked()["missing"]
    amended = {**quotes[0], "price": "1.90"}
    response = client.put(
        f"/api/v1/market/runs/{run['id']}/quotes",
        json={
            "expected_version": run["version"],
            "query": name,
            "quotes": [amended, *quotes[1:]],
        },
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/market/reports/{reports[0]['id']}").json()["data"]["stale_reason"]
    assert client.get(f"/api/v1/forecasts/{plan['id']}/replay").json() == previous
    client.headers["X-Workspace"] = "demo"
    assert client.get(f"/api/v1/forecasts/{plan['id']}").status_code == 404


def test_fee_proportion_target_and_net_revenue_are_separate():
    body = ForecastInput(
        price="20",
        c="5",
        f="2",
        b="0",
        t="1",
        a="2",
        r="0.1",
        l="4",
        fee_rate="0.05",
        target_margin="0.20",
        traffic={"days": 1, "daily_visits": 100, "low": "0", "base": "0.1", "high": "0.2"},
    )
    result = forecast(body, Decimal("14"), 2)
    assert result.minimum_target_price == "14.6600"
    assert result.reference_price_gap == "0.6600"
    assert result.reference_cost_reduction == "0.4800"
    assert result.unit_contribution == "7.5000" and result.scenarios[1].net_revenue is None
