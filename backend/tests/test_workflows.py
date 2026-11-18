from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import Entity, ImportBatch, Job, Link, Session, now
from app.schemas import CONTRACTS


def create(client, kind, payload):
    response = client.post("/api/v1/" + kind, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def workflow(client, evidence):
    ev, source = evidence
    unique = str(uuid4())
    product = create(client, "products", {"name": "流程测试" + unique, "category": "测试"})
    sku = create(
        client,
        "skus",
        {
            "product_id": product["id"],
            "name": "测试10只装",
            "spec_key": "10",
            "quantity": 10,
            "attributes": {"material": "金属", "size": "10mm"},
        },
    )
    offers = []
    for number in range(2):
        supplier = create(
            client, "suppliers", {"name": "测试供应商" + str(number), "external_id": unique + str(number)}
        )
        offers.append(
            create(
                client,
                "offers",
                {
                    "supplier_id": supplier["id"],
                    "sku_id": sku["id"],
                    "evidence_id": ev["id"],
                    "dropship": True,
                    "quantity": 10,
                    "unit_cost": "15",
                    "shipping": "4",
                    "service_fee": "1",
                    "region": "全国",
                    "lead_hours": 24,
                    "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
                    "return_terms": "已核验质量退货约定",
                    "verified": True,
                },
            )
        )
    candidate = create(
        client,
        "candidates",
        {"sku_id": sku["id"], "primary_offer_id": offers[0]["id"], "backup_offer_id": offers[1]["id"]},
    )
    for label, p, a, r, loss in [("NORMAL", "49.9", "3", 0.08, "8"), ("STRESS", "44.9", "7", 0.15, "10")]:
        create(
            client,
            "profits",
            {
                "candidate_id": candidate["id"],
                "evidence_id": ev["id"],
                "label": label,
                "p": p,
                "c": "15",
                "f": "4",
                "b": "1",
                "t": "1",
                "a": a,
                "r": r,
                "l": loss,
            },
        )
    return candidate, offers, ev, source


def move(client, candidate, target):
    response = client.post(
        f"/api/v1/candidates/{candidate['id']}/transition",
        json={
            "target_state": target,
            "expected_version": candidate["version"],
            "reason": "人工端到端流程验证",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_research_to_mature_profit_and_scale(client, evidence):
    candidate, offers, ev, source = workflow(client, evidence)
    candidate = move(client, candidate, "RESEARCHING")
    candidate = move(client, candidate, "READY_SAMPLE")
    for offer in offers:
        create(
            client,
            "samples",
            {
                "offer_id": offer["id"],
                "evidence_id": ev["id"],
                "result": "PASSED",
                "reason": "样品及履约核验通过",
            },
        )
    for code in ["IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"]:
        create(
            client,
            "risks",
            {
                "candidate_id": candidate["id"],
                "evidence_id": ev["id"],
                "risk_code": code,
                "status": "PASS",
                "reason": "证据核验通过",
            },
        )
    candidate = move(client, candidate, "SAMPLE_PASSED")
    candidate = move(client, candidate, "READY_TEST")
    stamp = datetime.now(UTC)
    experiment = create(
        client,
        "experiments",
        {
            "candidate_id": candidate["id"],
            "start_at": stamp.isoformat(),
            "end_at": (stamp + timedelta(days=14)).isoformat(),
            "budget": "20",
            "loss_limit": "10",
            "target_clicks": 100,
            "hypothesis": "达到样本后验证贡献为正",
        },
    )
    candidate = move(client, candidate, "TESTING")
    from zoneinfo import ZoneInfo

    create(
        client,
        "funnels",
        {
            "experiment_id": experiment["id"],
            "evidence_id": ev["id"],
            "business_date": stamp.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "channel": "搜索",
            "impressions": 1000,
            "clicks": 120,
            "paid_orders": 1,
            "ad_spend": "6",
        },
    )
    create(
        client,
        "orders",
        {
            "experiment_id": experiment["id"],
            "source_id": source["id"],
            "evidence_id": ev["id"],
            "external_order_id": str(uuid4()),
            "line_id": "1",
            "paid_at": stamp.isoformat(),
            "channel": "搜索",
            "revenue": "39.9",
            "purchase": "15",
            "purchase_refund": "0",
            "shipping": "4",
            "packaging": "1",
            "net_fees": "1",
            "advertising": None,
            "other_net_cost": "0",
            "mature": True,
            "settled": True,
        },
    )
    before = client.get(f"/api/v1/experiments/{experiment['id']}/summary").json()
    assert before["incomplete_orders"] == 1 and before["mature_orders"] == 0
    assert client.post(f"/api/v1/experiments/{experiment['id']}/allocate-ads").status_code == 200
    summary = client.get(f"/api/v1/experiments/{experiment['id']}/summary").json()
    assert summary["mature_orders"] == 1 and summary["mature_contribution"] == "12.9000"
    assert summary["recommendation"] == "REVIEW_SCALE"
    candidate = move(client, candidate, "SCALE")
    assert candidate["data"]["state"] == "SCALE"


def test_expired_offer_and_revised_evidence_invalidate_gate(client, evidence):
    candidate, offers, ev, source = workflow(client, evidence)
    payload = {k: v for k, v in offers[0]["data"].items() if k in CONTRACTS["offers"].model_fields}
    payload["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    response = client.put(
        "/api/v1/offers/" + offers[0]["id"], json={"expected_version": 1, "payload": payload}
    )
    assert response.status_code == 200
    requirements = client.get("/api/v1/candidates/" + candidate["id"] + "/research").json()["requirements"]
    assert "主供有效报价" in requirements["missing"]
    payload = {k: v for k, v in ev["data"].items() if k in CONTRACTS["evidence"].model_fields}
    payload["note"] = "证据说明已修订，需要重新确认"
    assert (
        client.put(
            "/api/v1/evidence/" + ev["id"], json={"expected_version": 1, "payload": payload}
        ).status_code
        == 200
    )
    requirements = client.get("/api/v1/candidates/" + candidate["id"] + "/research").json()["requirements"]
    assert "主供的有效证据" in requirements["missing"]


def test_historical_score_marks_changed_evidence_and_still_replays(client, evidence):
    from app.selection import compute_score, run_score

    candidate, offers, ev, source = workflow(client, evidence)
    with Session.begin() as db:
        score = run_score(db, "demo", candidate["id"])
        score_id, inputs, result = score.id, score.data["input_snapshot"], score.data["factors"]
    url = "/api/v1/candidates/" + candidate["id"] + "/research"
    assert client.get(url).json()["score_stale"] is False
    payload = {k: v for k, v in ev["data"].items() if k in CONTRACTS["evidence"].model_fields}
    payload["note"] = "证据已修订，历史评分保留但需要重新核验"
    response = client.put("/api/v1/evidence/" + ev["id"], json={"expected_version": 1, "payload": payload})
    assert response.status_code == 200
    current = client.get(url).json()
    assert current["score_stale"] is True and current["score"]["id"] == score_id
    assert compute_score(inputs)["factors"] == result


def test_risk_clearance_requires_new_evidence(client, evidence):
    candidate, offers, ev, source = workflow(client, evidence)
    payload = {
        "candidate_id": candidate["id"],
        "evidence_id": ev["id"],
        "risk_code": "IP",
        "status": "BLOCKED",
        "reason": "发现授权问题",
    }
    create(client, "risks", payload)
    payload.update(status="PASS", reason="希望解除")
    rejected = client.post("/api/v1/risks", json=payload)
    assert rejected.status_code == 409 and rejected.json()["code"] == "NEW_EVIDENCE_REQUIRED"
    new_ev = create(
        client,
        "evidence",
        {
            "source_id": source["id"],
            "locator": "test://new-permission",
            "observed_at": datetime.now(UTC).isoformat(),
            "note": "新增授权证明",
        },
    )
    payload["evidence_id"] = new_ev["id"]
    assert client.post("/api/v1/risks", json=payload).status_code == 201


def test_database_foreign_key_blocks_cross_space_link(client, evidence):
    ev, _source = evidence
    client.headers["X-Workspace"] = "live"
    foreign = create(client, "products", {"name": "跨空间外键测试" + str(uuid4()), "category": "测试"})
    with pytest.raises(IntegrityError), Session.begin() as db:
        db.add(Link(workspace="demo", from_id=ev["id"], field="test", to_id=foreign["id"]))


def test_future_quote_rejected(client, evidence):
    ev, _ = evidence
    listing = client.get("/api/v1/listings?page_size=1").json()["items"][0]
    response = client.post(
        "/api/v1/prices",
        json={
            "listing_id": listing["id"],
            "evidence_id": ev["id"],
            "observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "region": "全国",
            "quantity": listing["data"]["quantity"],
            "price": "1",
            "shipping": "0",
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "INVALID_TIME"


def import_job(client, source, rows):
    raw = "keyword,category,search_index,listing_count\n" + "".join(
        f"retry-{i},测试,1000,100\n" for i in range(rows)
    )
    uploaded = client.post(
        "/api/v1/imports/preview",
        data={"source_id": source["id"], "kind": "keywords"},
        files={"file": ("retry.csv", raw.encode(), "text/csv")},
    ).json()
    stamp = datetime.now(UTC) - timedelta(minutes=1)
    end = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    request = {
        "mapping": {k: k for k in ["keyword", "category", "search_index", "listing_count"]},
        "defaults": {
            "window_start": (end - timedelta(days=7)).isoformat(),
            "window_end": end.isoformat(),
            "observed_at": stamp.isoformat(),
            "metric_definition": "retry",
        },
    }
    job = client.post("/api/v1/imports/" + uploaded["id"] + "/confirm", json=request).json()
    return job, uploaded, request


def test_retry_resumes_committed_checkpoint_without_double_count(client, evidence, monkeypatch):
    _ev, source = evidence
    job, batch, request = import_job(client, source, 2100)
    import app.ingestion as ingestion
    from app.worker import run_once

    original = ingestion.import_chunk
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated worker interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(ingestion, "import_chunk", interrupted)
    run_once()
    with Session.begin() as db:
        current = db.get(Job, job["id"])
        assert current.status == "QUEUED"
        assert db.get(ImportBatch, batch["id"]).accepted == 1000
        current.run_after = now() - timedelta(seconds=1)
    monkeypatch.setattr(ingestion, "import_chunk", original)
    run_once()
    result = client.get("/api/v1/jobs/" + job["id"]).json()
    assert result["status"] == "SUCCEEDED", result
    assert result["result"]["accepted"] == 2100
    assert result["result"]["unchanged"] == 0
    repeated = client.post("/api/v1/imports/" + batch["id"] + "/confirm", json=request)
    assert repeated.status_code == 202 and repeated.json()["id"] == job["id"]


def test_excess_rows_fail_before_business_commit(client, evidence, monkeypatch):
    ev, source = evidence
    import app.ingestion as ingestion

    monkeypatch.setattr(ingestion, "MAX_ROWS", 1)
    job, batch, _ = import_job(client, source, 2)
    from app.worker import run_once

    run_once()
    assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "FAILED"
    with Session() as db:
        assert not list(
            db.scalars(
                select(Entity).where(
                    Entity.kind == "keywords", Entity.data["source_id"].as_string() == source["id"]
                )
            )
        )


def test_fenced_worker_cannot_commit_after_reclaim(client):
    from app.ingestion import assert_lease
    from app.errors import BusinessError

    with Session.begin() as db:
        job = Job(
            workspace="demo",
            kind="score",
            idem_key=str(uuid4()),
            status="RUNNING",
            lease_token="new-owner",
            payload={},
            lease_until=now() + timedelta(seconds=120),
        )
        db.add(job)
        db.flush()
        job_id = job.id
    with pytest.raises(BusinessError, match="接管"), Session.begin() as db:
        assert_lease(db, job_id, "old-owner")
    with Session.begin() as db:
        db.get(Job, job_id).status = "CANCELLED"


def test_abandoned_last_attempt_becomes_failed(client):
    from app.worker import claim

    with Session.begin() as db:
        job = Job(
            workspace="demo",
            kind="score",
            idem_key=str(uuid4()),
            status="RUNNING",
            attempts=3,
            lease_until=now() - timedelta(seconds=1),
            payload={},
        )
        db.add(job)
        db.flush()
        job_id = job.id
    claim()
    with Session() as db:
        assert db.get(Job, job_id).status == "FAILED"


def test_lower_bound_sales_signal_remains_distinct_from_reviews(client, evidence):
    ev, _ = evidence
    listing = client.get("/api/v1/listings?page_size=1").json()["items"][0]
    payload = {
        "listing_id": listing["id"],
        "evidence_id": ev["id"],
        "metric_kind": "SOLD",
        "unit": "ITEMS",
        "raw_value": "1000+",
        "value": "1000",
        "precision": "lower_bound",
        "observed_at": datetime.now(UTC).isoformat(),
    }
    row = create(client, "signals", payload)
    assert row["data"]["precision"] == "lower_bound" and row["data"]["raw_value"] == "1000+"
    payload.update(metric_kind="REVIEW_COUNT", unit="ITEMS")
    assert client.post("/api/v1/signals", json=payload).status_code == 422
