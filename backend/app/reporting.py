from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db import Entity, now
from app.domain import COST_FIELDS, distribute, iso, money, order_contribution, ratio
from app.errors import BusinessError
from app.store import audit, get, persist, rows
from app.workflows import gate


def experiment_summary(db, workspace, experiment_id):
    experiment = get(db, workspace, experiment_id, "experiments")
    candidate = get(db, workspace, experiment.data["candidate_id"], "candidates")
    orders = rows(db, workspace, "orders", experiment_id=experiment_id)
    funnel = rows(db, workspace, "funnels", experiment_id=experiment_id)
    matured, provisional, incomplete = [], [], []
    for order in orders:
        value = order_contribution(order.data)
        if value is None:
            incomplete.append(order)
        elif order.data["mature"] and order.data["settled"]:
            matured.append((order, value))
        else:
            provisional.append((order, value))
    contribution = sum((value for _, value in matured), D(0))
    ad_spend = sum((D(row.data["ad_spend"]) for row in funnel if row.data.get("ad_spend") is not None), D(0))
    clicks = sum(row.data.get("clicks") or 0 for row in funnel)
    impressions = sum(row.data.get("impressions") or 0 for row in funnel)
    paid_orders = sum(row.data.get("paid_orders") or 0 for row in funnel)
    remaining = D(experiment.data["budget"]) - ad_spend
    validation = gate(db, workspace, candidate)
    pause_reasons = []
    if remaining <= 0:
        pause_reasons.append("测款广告预算已用完")
    if contribution < -D(experiment.data["loss_limit"]):
        pause_reasons.append("成熟订单亏损超过止损线")
    if validation["gate"] != "PASS":
        pause_reasons.append("审核或证据需要重新核验")
    if pause_reasons:
        recommendation = "PAUSE"
    elif clicks < experiment.data["target_clicks"] or not matured or incomplete or provisional:
        recommendation = "INCONCLUSIVE"
    elif contribution > 0:
        recommendation = "REVIEW_SCALE"
    else:
        recommendation = "OPTIMIZE"
    return {
        "experiment_id": experiment.id,
        "status": experiment.data["status"],
        "impressions": impressions,
        "clicks": clicks,
        "paid_orders": paid_orders,
        "ctr": float(ratio(clicks, impressions)) if impressions else None,
        "conversion": float(ratio(paid_orders, clicks)) if clicks else None,
        "ad_spend": money(ad_spend),
        "remaining_budget": money(remaining),
        "expired": iso(experiment.data["end_at"]) <= now(),
        "mature_orders": len(matured),
        "provisional_orders": len(provisional),
        "incomplete_orders": len(incomplete),
        "mature_contribution": money(contribution),
        "mature_per_order": money(contribution / len(matured)) if matured else None,
        "provisional_contribution": money(sum((v for _, v in provisional), D(0))),
        "recommendation": recommendation,
        "pause_reasons": pause_reasons,
        "missing_costs": [
            {"id": r.id, "fields": [k for k in COST_FIELDS if r.data.get(k) is None]} for r in incomplete
        ],
    }


def allocate_ads(db, workspace, experiment_id):
    get(db, workspace, experiment_id, "experiments")
    orders = rows(db, workspace, "orders", experiment_id=experiment_id)
    result = []
    for funnel in rows(db, workspace, "funnels", experiment_id=experiment_id):
        if funnel.data.get("ad_spend") is None:
            continue
        matching = [
            o
            for o in orders
            if o.data["channel"] == funnel.data["channel"]
            and iso(o.data["paid_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            == funnel.data["business_date"]
        ]
        # Do not silently spread spend across a partial order set.
        unique_orders = {o.data["external_order_id"] for o in matching}
        if funnel.data.get("paid_orders") != len(unique_orders):
            raise BusinessError(
                "ORDER_COVERAGE",
                "渠道支付订单数与已导入订单不一致，不能分摊",
                409,
                {"date": funnel.data["business_date"], "channel": funnel.data["channel"]},
            )
        if not matching and D(funnel.data["ad_spend"]) != 0:
            raise BusinessError("NO_PAID_ORDERS", "无支付订单，广告费保留为未分摊经营支出", 409)
        allocation = distribute(funnel.data["ad_spend"], unique_orders)
        for order_key, amount in allocation.items():
            lines = [o for o in matching if o.data["external_order_id"] == order_key]
            # Within one order, equally split its candidate lines; remainder goes to the last stable ID.
            line_allocations = distribute(amount, [o.id for o in lines])
            for row in lines:
                data = {
                    **row.data,
                    "advertising": money(line_allocations[row.id]),
                    "ad_allocation": {
                        "version": "paid-day-channel-equal-v1",
                        "funnel_id": funnel.id,
                        "funnel_version": funnel.version,
                    },
                }
                value = order_contribution(data)
                data.update(
                    contribution=money(value) if value is not None else None, cost_complete=value is not None
                )
                persist(
                    db,
                    workspace,
                    "orders",
                    data,
                    entity=row,
                    expected_version=row.version,
                    action="allocate_ads",
                )
                result.append(row.id)
    audit(db, workspace, experiment_id, "allocation_completed", {"order_lines": len(result)})
    return {"updated": len(result), "version": "paid-day-channel-equal-v1"}


def dashboard(db, workspace, start=None, end=None, category=None):
    counts = dict(
        db.execute(
            select(Entity.kind, func.count()).where(Entity.workspace == workspace).group_by(Entity.kind)
        ).all()
    )
    candidates = rows(db, workspace, "candidates")
    if category:
        candidates = [
            c
            for c in candidates
            if get(db, workspace, get(db, workspace, c.data["sku_id"]).data["product_id"]).data["category"]
            == category
        ]
    candidate_ids = {c.id for c in candidates}
    states = {}
    todos = []
    for candidate in candidates:
        state = candidate.data["state"]
        states[state] = states.get(state, 0) + 1
        result = gate(db, workspace, candidate)
        if result["gate"] != "PASS":
            todos.append({"id": candidate.id, "state": state, **result})
    experiments = rows(db, workspace, "experiments")
    if category:
        experiments = [e for e in experiments if e.data["candidate_id"] in candidate_ids]
    experiment_ids = {e.id for e in experiments}
    orders = [o for o in rows(db, workspace, "orders") if o.data["experiment_id"] in experiment_ids]
    if start:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        orders = [o for o in orders if iso(o.data["paid_at"]) >= start_dt]
    if end:
        end_dt = datetime.fromisoformat(end).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        orders = [o for o in orders if iso(o.data["paid_at"]) < end_dt]
    mature = [
        o for o in orders if o.data["mature"] and o.data["settled"] and order_contribution(o.data) is not None
    ]
    totals = sum((order_contribution(o.data) for o in mature), D(0))
    return {
        "counts": counts,
        "candidate_count": len(candidates),
        "states": states,
        "todos": todos[:30],
        "mature_contribution": money(totals),
        "mature_orders": len(mature),
        "budget": money(
            sum(
                (D(e.data["budget"]) for e in experiments if e.data["status"] in {"PLANNED", "RUNNING"}), D(0)
            )
        ),
        "unsettled_orders": sum(not o.data["settled"] for o in orders),
        "experiments": [
            {"id": e.id, "data": e.data, "summary": experiment_summary(db, workspace, e.id)}
            for e in experiments[:10]
        ],
        "history": public_totals(db, workspace) if workspace == "public" else None,
    }


def public_totals(db, workspace):
    # Numeric SQL aggregation avoids materializing the full public dataset in the API.
    from sqlalchemy import Numeric, cast

    base = select(Entity).where(Entity.workspace == workspace, Entity.kind == "transactions")
    count = db.scalar(select(func.count()).select_from(base.subquery()))
    amount = db.scalar(
        select(func.sum(cast(Entity.data["amount"].as_string(), Numeric))).where(
            Entity.workspace == workspace, Entity.kind == "transactions"
        )
    ) or D(0)
    quantity = db.scalar(
        select(func.sum(cast(Entity.data["quantity"].as_string(), Numeric))).where(
            Entity.workspace == workspace, Entity.kind == "transactions"
        )
    ) or D(0)
    cancelled = db.scalar(
        select(func.count()).where(
            Entity.workspace == workspace,
            Entity.kind == "transactions",
            Entity.data["cancelled"].as_boolean().is_(True),
        )
    )
    return {
        "rows": count,
        "quantity": int(quantity),
        "amount": money(amount),
        "currency": "GBP",
        "unmatched_cancellations": cancelled,
        "contribution": None,
    }
