#!/usr/bin/env python3
"""Measure the ranked API against 1,000 evidence-backed synthetic candidates."""

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import timedelta

from sqlalchemy import insert
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def seed(db, workspace, count):
    from app.db import Entity, new_id, now
    from app.seed import defaults
    from app.store import ReadSnapshot, latest, save
    from app.workflows import gate

    if latest(db, workspace, "candidates"):
        raise ValueError("使用专用空测试库运行推荐压测")
    defaults(db)
    proof_time = now() - timedelta(minutes=1)
    expires = (proof_time + timedelta(days=3)).isoformat()
    source, _ = save(
        db,
        workspace,
        "sources",
        {
            "name": "推荐性能合成来源",
            "platform": "BENCHMARK",
            "permission_scope": ["*"],
            "permission_note": "完全合成的独立测试库数据",
        },
    )
    proof, _ = save(
        db,
        workspace,
        "evidence",
        {
            "source_id": source.id,
            "locator": "benchmark://opportunities",
            "observed_at": proof_time.isoformat(),
            "expires_at": expires,
        },
    )
    strategy = latest(db, workspace, "strategies")
    evidence = {"evidence_id": proof.id, "evidence_version": 1}
    entities, candidates = [], []

    def add(kind, **data):
        row = Entity(
            id=new_id(),
            kind=kind,
            workspace=workspace,
            version=1,
            data=data,
            created_at=proof_time,
            updated_at=proof_time,
        )
        entities.append(row)
        return row.id

    for index in range(count):
        category = "口腔护理" if index % 2 == 0 else "办公整理"
        name = f"{category}研究商品 {index:04}"
        attributes = {"material": "PBT", "size": "18cm"}
        product_id = add("products", name=name, category=category)
        sku_id = add(
            "skus", name=name, product_id=product_id, quantity=2, attributes=attributes
        )
        keyword_id = add(
            "keywords",
            **evidence,
            source_id=source.id,
            keyword=name,
            category=category,
            metric_definition="bench-index",
            search_index="3000",
            conversion=0.04,
            window_end=(proof_time - timedelta(days=1)).isoformat(),
            window_start=(proof_time - timedelta(days=8)).isoformat(),
            observed_at=proof_time.isoformat(),
        )
        mode = "STOCK" if index % 2 == 0 else "DROPSHIP"
        offers = [
            add(
                "offers",
                **evidence,
                supplier_id=side,
                sku_id=sku_id,
                quantity=2,
                procurement_mode=mode,
                dropship=mode == "DROPSHIP",
                unit_cost="8",
                shipping="0" if mode == "STOCK" else "3",
                outbound_shipping="3",
                batch_quantity=20,
                supplier_pack=2,
                min_order=1,
                service_fee="0",
                verified=True,
                region="全国",
                expires_at=expires,
            )
            for side in ("primary", "backup")
        ]
        candidate_id = add(
            "candidates",
            sku_id=sku_id,
            keyword_id=keyword_id,
            procurement_mode=mode,
            state="READY_TEST",
            primary_offer_id=offers[0],
            backup_offer_id=offers[1],
        )
        candidates.append(candidate_id)
        for offer_id in offers:
            add("samples", **evidence, offer_id=offer_id, result="PASSED")
        for code in ("IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"):
            add(
                "risks",
                **evidence,
                candidate_id=candidate_id,
                risk_code=code,
                status="PASS",
            )
        for label, price in (("NORMAL", "29.90"), ("STRESS", "25.90")):
            add(
                "profits",
                **evidence,
                candidate_id=candidate_id,
                label=label,
                procurement_mode=mode,
                offer_version={"id": offers[0], "version": 1},
                p=price,
                c="8",
                f="3",
                b="1",
                t="1",
                a="2",
                r=0.05,
                l="5",
            )
        for platform, price in (
            ("淘宝", "28.90"),
            ("京东", "29.90"),
            ("拼多多", "27.90"),
        ):
            listing = add(
                "listings",
                title=name,
                platform=platform,
                quantity=2,
                attributes=attributes,
            )
            add(
                "matches",
                **evidence,
                listing_id=listing,
                sku_id=sku_id,
                state="MATCHED",
                sku_version=1,
                listing_version=1,
            )
            add(
                "prices",
                **evidence,
                listing_id=listing,
                price=price,
                shipping="0",
                region="全国",
                currency="CNY",
                quantity=2,
                observed_at=proof_time.isoformat(),
                price_kind="LIST",
            )
    snapshot = ReadSnapshot(workspace, [*entities, source, proof, strategy])
    for identity in candidates:
        candidate = snapshot.by_id[identity]
        requirements = gate(snapshot, workspace, candidate)
        assert requirements["gate"] == "PASS", requirements
        add(
            "scores",
            candidate_id=identity,
            strategy_id=strategy.id,
            input_snapshot={"gate": requirements},
            score=86,
            grade="S",
            factors={"demand": 85, "growth": 80},
            coverage=1,
            records=[
                {"id": ref, "version": 1, "data": snapshot.by_id[ref].data}
                for ref in (identity, candidate.data["keyword_id"], proof.id, source.id)
            ],
        )
    db.execute(
        insert(Entity),
        [
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "workspace",
                    "kind",
                    "data",
                    "version",
                    "created_at",
                    "updated_at",
                )
            }
            for row in entities
        ],
    )
    return len(entities)


def percentiles(samples):
    ordered = sorted(samples)
    return {
        "requests": len(samples),
        "p50_ms": round(ordered[math.ceil(len(samples) * 0.50) - 1], 2),
        "p95_ms": round(ordered[math.ceil(len(samples) * 0.95) - 1], 2),
        "max_ms": round(max(samples), 2),
    }


def main():
    url = os.environ["TEST_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        raise SystemExit("需要独立的 _test 数据库")
    os.environ.update(DATABASE_URL=url, OWNER_PASSWORD="opportunity-benchmark-local")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        check=True,
    )
    from fastapi.testclient import TestClient

    from app.db import Session
    from app.main import app

    with Session.begin() as db:
        records = seed(db, "live", 1000)
    output = {
        "candidates": 1000,
        "synthetic_records": records,
        "page_size": 12,
        "method": "PostgreSQL + authenticated FastAPI ASGI, 3 warmups then 50 sequential requests per query",
    }
    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login", json={"password": os.environ["OWNER_PASSWORD"]}
        ).raise_for_status()
        client.headers["X-Workspace"] = "live"
        for label, search, expected in [
            ("first_page", "", 1000),
            ("search", "口腔护理", 500),
        ]:
            elapsed = []
            for index in range(53):
                start = time.perf_counter()
                response = client.get(
                    "/api/v1/opportunities", params={"page_size": 12, "search": search}
                )
                response.raise_for_status()
                values = response.json()
                assert values["total"] == expected and len(values["items"]) == 12
                assert all(item["level"] == "PRIORITY" for item in values["items"])
                if index >= 3:
                    elapsed.append((time.perf_counter() - start) * 1000)
            output[label] = percentiles(elapsed)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
