from copy import deepcopy
from datetime import timedelta

import yaml

from app.config import ROOT, WORKSPACES
from app.db import Session, now
from app.schemas import MatchReview, Transition
from app.selection import run_score
from app.store import latest, save
from app.workflows import review_match, transition


def defaults(db):
    with (ROOT / "config" / "strategy_config.yaml").open() as f:
        base = yaml.safe_load(f)
    for workspace in WORKSPACES:
        if latest(db, workspace, "strategies"):
            continue
        config = deepcopy(base)
        if workspace == "demo":
            config["demo_only"] = True
            config["version"] += "-demo"
            config["score"]["fallback_ranges"] = {
                "search_index": [100, 5000],
                "conversion": [0, 0.08],
                "opportunity": [0, 10],
                "head_share": [0, 1],
                "mall_click_share": [0, 1],
                "contribution": [0, 30],
                "contribution_rate": [0, 0.6],
                "stress_contribution": [0, 15],
                "growth_7d": [0, 0.4],
                "growth_30d": [0, 0.7],
                "growth_gap": [0, 0.35],
                "loss_rate": [0, 0.2],
            }
        save(
            db,
            workspace,
            "strategies",
            {
                "name": "演示初始参数" if workspace == "demo" else "初始研究参数",
                "version": config["version"],
                "config": config,
            },
        )


def seed_demo():
    with Session.begin() as db:
        defaults(db)
        if latest(db, "demo", "candidates"):
            return
        ws = "demo"
        create = lambda kind, data: save(db, ws, kind, data)[0]
        stamp = now() - timedelta(minutes=1)
        evidence_end = now() + timedelta(days=30)
        source = create(
            "sources",
            {
                "name": "CommerceAI 模拟研究数据",
                "platform": "DEMO",
                "permission_scope": ["*"],
                "permission_note": "完全合成的测试记录，不代表当前市场行情",
                "license": "synthetic",
            },
        )
        evidence = create(
            "evidence",
            {
                "source_id": source.id,
                "locator": "demo://research-v1.1",
                "observed_at": stamp.isoformat(),
                "expires_at": evidence_end.isoformat(),
                "note": "模拟规格、报价、样品与审核证据",
            },
        )
        ev = {"evidence_id": evidence.id}
        end = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        keywords = []
        names = ["桌下理线槽", "免打孔线缆夹", "电子元件收纳盒", "模型工具整理架"]
        for index in range(40):
            name = names[index % 4] + (" " + str(index + 1) if index >= 4 else "")
            selected = None
            for days, sequence in [(7, 0), (7, 1), (7, 2), (7, 3), (30, 0), (30, 1)]:
                last = end - timedelta(days=days * sequence)
                row = create(
                    "keywords",
                    {
                        "source_id": source.id,
                        **ev,
                        "keyword": name,
                        "category": "办公整理",
                        "window_start": (last - timedelta(days=days)).isoformat(),
                        "window_end": last.isoformat(),
                        "observed_at": stamp.isoformat(),
                        "metric_definition": "demo_search_index_v1",
                        "search_index": str(
                            round((1000 + index * 100) / (1.32**sequence if days == 7 else 1.6**sequence))
                        ),
                        "listing_count": round((300 + index * 5) / (1.04**sequence)),
                        "conversion": 0.035 + index * 0.001,
                        "mall_click_share": 0.30 - index * 0.003,
                    },
                )
                if days == 7 and sequence == 0:
                    selected = row
            keywords.append(selected)
        suppliers = [
            create("suppliers", {"name": "模拟" + name, "external_id": "demo-" + str(i), "platform": "1688"})
            for i, name in enumerate(["主供工厂", "备供工厂"])
        ]
        for index, keyword_index in enumerate([10, 20, 30, 38]):
            product = create("products", {"name": names[index], "category": "办公整理", "brand": "无品牌"})
            sku = create(
                "skus",
                {
                    "product_id": product.id,
                    "name": names[index] + " 10只装",
                    "spec_key": "10mm-10pcs",
                    "quantity": 10,
                    "attributes": {"material": "硅胶", "size": "10mm", "brand": "无品牌"},
                },
            )
            offers = [
                create(
                    "offers",
                    {
                        "supplier_id": supplier.id,
                        "sku_id": sku.id,
                        **ev,
                        "dropship": True,
                        "quantity": 10,
                        "unit_cost": "15" if i == 0 else "16",
                        "shipping": "4",
                        "service_fee": "1",
                        "region": "全国",
                        "lead_hours": 24 + i * 12,
                        "expires_at": evidence_end.isoformat(),
                        "return_terms": "模拟：质量问题退货及运费责任已确认",
                        "verified": True,
                    },
                )
                for i, supplier in enumerate(suppliers)
            ]
            candidate = create(
                "candidates",
                {
                    "sku_id": sku.id,
                    "keyword_id": keywords[keyword_index].id,
                    "primary_offer_id": offers[0].id,
                    "backup_offer_id": offers[1].id,
                    "note": [
                        "等待购买样品",
                        "审核已完成，可以建立测款计划",
                        "样品不合格，已阻断",
                        "正在测款，已有成熟订单",
                    ][index],
                },
            )
            for i, platform in enumerate(["淘宝", "拼多多", "京东", "抖音", "淘宝", "1688"]):
                listing = create(
                    "listings",
                    {
                        "platform": platform,
                        "external_id": f"demo-{index}-{i}",
                        "external_sku_id": "sku10",
                        "title": sku.data["name"],
                        "quantity": 10,
                        "attributes": sku.data["attributes"],
                    },
                )
                match = create(
                    "matches", {"listing_id": listing.id, "sku_id": sku.id, **ev, "reason": "模拟规格一致"}
                )
                review_match(
                    db,
                    ws,
                    match.id,
                    MatchReview(
                        state="MATCHED",
                        evidence_id=evidence.id,
                        reason="模拟人工核验一致",
                        expected_version=match.version,
                    ),
                )
                create(
                    "prices",
                    {
                        "listing_id": listing.id,
                        **ev,
                        "observed_at": stamp.isoformat(),
                        "price_kind": "DROPSHIP" if platform == "1688" else "LIST",
                        "quantity": 10,
                        "region": "全国",
                        "price": str([49.9, 45.9, 59.9, 48.9, 52.9, 15][i]),
                        "shipping": "0",
                    },
                )
            for label, p, a, r, loss in [
                ("NORMAL", "49.9", "3", 0.08, "8"),
                ("STRESS", "44.9", "7", 0.15, "10"),
            ]:
                create(
                    "profits",
                    {
                        "candidate_id": candidate.id,
                        **ev,
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
            create(
                "assessments",
                {
                    "candidate_id": candidate.id,
                    **ev,
                    "reason": "模拟人工分项核验",
                    "fulfillment_score": 92,
                    "quality_score": None if index == 0 else 94,
                    "backup_score": 90,
                    "usage_risk_score": 92,
                    "differentiation_score": 85,
                    "head_share": 0.2,
                    "head_share_sample_count": 12,
                    "head_share_precision": "exact",
                    "distinct_reviews": 25,
                    "pain_verified": True,
                    "pain_solvable": True,
                    "bundle_fulfillment_verified": True,
                    "single_contribution": "12",
                    "bundle_contribution": "20",
                },
            )
            for code in ["IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"]:
                create(
                    "risks",
                    {
                        "candidate_id": candidate.id,
                        **ev,
                        "risk_code": code,
                        "status": "PASS",
                        "reason": "模拟人工审核已通过",
                    },
                )
            if index != 0:
                for offer in offers:
                    create(
                        "samples",
                        {
                            "offer_id": offer.id,
                            **ev,
                            "result": "FAILED" if index == 2 else "PASSED",
                            "reason": "模拟：材质不合格" if index == 2 else "模拟：尺寸、包装及履约通过",
                        },
                    )
            states = ["RESEARCHING"]
            if index in {1, 3}:
                states += ["READY_SAMPLE", "SAMPLE_PASSED", "READY_TEST"]
            for state in states:
                candidate = transition(
                    db,
                    ws,
                    candidate.id,
                    Transition(
                        target_state=state, expected_version=candidate.version, reason="初始化模拟流程"
                    ),
                )
            run_score(db, ws, candidate.id)
            if index == 3:
                experiment = create(
                    "experiments",
                    {
                        "candidate_id": candidate.id,
                        "start_at": (stamp - timedelta(days=2)).isoformat(),
                        "end_at": (stamp + timedelta(days=12)).isoformat(),
                        "budget": "100",
                        "loss_limit": "50",
                        "target_clicks": 100,
                        "hypothesis": "模拟：小批验证桌面整理场景",
                    },
                )
                transition(
                    db,
                    ws,
                    candidate.id,
                    Transition(
                        target_state="TESTING", expected_version=candidate.version, reason="模拟人工开始测款"
                    ),
                )
                day = stamp.date().isoformat()
                create(
                    "funnels",
                    {
                        "experiment_id": experiment.id,
                        **ev,
                        "business_date": day,
                        "channel": "搜索",
                        "impressions": 2500,
                        "clicks": 120,
                        "paid_orders": 3,
                        "ad_spend": "9",
                    },
                )
                for number in range(3):
                    create(
                        "orders",
                        {
                            "source_id": source.id,
                            **ev,
                            "experiment_id": experiment.id,
                            "external_order_id": f"demo-order-{number}",
                            "line_id": "1",
                            "paid_at": stamp.isoformat(),
                            "channel": "搜索",
                            "revenue": "49.90",
                            "purchase": "15",
                            "purchase_refund": "0",
                            "shipping": "4",
                            "packaging": "1",
                            "net_fees": "1",
                            "advertising": "3",
                            "other_net_cost": "0",
                            "mature": number < 2,
                            "settled": number < 2,
                        },
                    )


if __name__ == "__main__":
    seed_demo()
    print("演示数据初始化完成；未改动公开历史与真实经营数据。")
