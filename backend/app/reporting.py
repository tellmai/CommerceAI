from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db import Entity, now
from app.domain import COST_FIELDS, distribute, iso, money, order_contribution, ratio
from app.errors import BusinessError
from app.store import audit, get, persist, require_evidence, rows
from app.workflows import gate


BEIJING = ZoneInfo("Asia/Shanghai")


def paid_day(row):
    return iso(row.data["paid_at"]).astimezone(BEIJING).date()


def order_key(row):
    return row.data["source_id"], row.data["external_order_id"]


def experiment_orders(db, workspace, experiment):
    start, end = (iso(experiment.data[key]) for key in ("start_at", "end_at"))
    return [
        row
        for row in rows(db, workspace, "orders", experiment_id=experiment.id)
        if start <= iso(row.data["paid_at"]) < end
    ]


def experiment_funnels(db, workspace, experiment):
    start, end = (iso(experiment.data[key]).astimezone(BEIJING) for key in ("start_at", "end_at"))
    return [
        row
        for row in rows(db, workspace, "funnels", experiment_id=experiment.id)
        if start.date().isoformat() <= row.data["business_date"] <= end.date().isoformat()
        and (end.time() != datetime.min.time() or row.data["business_date"] != end.date().isoformat())
    ]


def group_orders(orders):
    groups = defaultdict(list)
    for row in orders:
        groups[order_key(row)].append(row)
    return groups


def summarize_orders(orders):
    groups = group_orders(orders)
    matured, provisional, incomplete = [], [], []
    for lines in groups.values():
        values = [(row, order_contribution(row.data)) for row in lines]
        if any(value is None for _, value in values):
            incomplete.append([row for row, value in values if value is None])
        elif all(row.data["mature"] and row.data["settled"] for row in lines):
            matured.append(values)
        else:
            provisional.append(values)
    return groups, matured, provisional, incomplete


def required_days(start, end):
    return {(start + timedelta(days=days)).isoformat() for days in range((end - start).days)}


def total_complete(values):
    return sum(values) if values and all(value is not None for value in values) else None


def reconcile_channel(db, workspace, orders, funnels, dates):
    covered_days = {row.data["business_date"] for row in funnels}
    reasons, versions, counted = [], {}, defaultdict(set)
    if dates != covered_days:
        reasons.append("部分日期缺少渠道漏斗记录")
    for row in orders:
        day = paid_day(row).isoformat()
        counted[day].add(order_key(row))
        versions[row.id] = row.version
    for row in [*orders, *funnels]:
        try:
            proof = require_evidence(
                db, workspace, row.data["evidence_id"], row.kind, row.data.get("evidence_version")
            )
            source = get(db, workspace, proof.data["source_id"], "sources")
            versions.update({row.id: row.version, proof.id: proof.version, source.id: source.version})
        except BusinessError:
            reasons.append("渠道或订单证据已失效")
            break
    if any(row.data.get("paid_orders") != len(counted[row.data["business_date"]]) for row in funnels):
        reasons.append("渠道支付订单数与订单账本未对齐")
    if sum(len(keys) for keys in counted.values()) != len(group_orders(orders)):
        reasons.append("同一支付订单跨日期重复，需核验支付时间")
    return reasons, versions


def settled_traffic(db, workspace, experiment):
    """Only complete, reconciled observations can seed future traffic assumptions."""
    from app.forecasts import beijing_dates

    date_window = beijing_dates(experiment.data)
    if date_window is None or iso(experiment.data["end_at"]) > now():
        return []
    dates = required_days(*date_window)
    funnels = experiment_funnels(db, workspace, experiment)
    orders = experiment_orders(db, workspace, experiment)
    results = []
    for channel in sorted({row.data["channel"] for row in funnels}):
        scoped = [row for row in funnels if row.data["channel"] == channel]
        scoped_orders = [row for row in orders if row.data["channel"] == channel]
        reasons, versions = reconcile_channel(db, workspace, scoped_orders, scoped, dates)
        clicks = total_complete([row.data.get("clicks") for row in scoped])
        paid = len(group_orders(scoped_orders))
        if not dates or reasons or not clicks or paid > clicks:
            continue
        results.append(
            {
                "channel": channel,
                "daily_clicks": clicks // len(dates),
                "clicks": clicks,
                "paid_orders": paid,
                "conversion": money(D(paid) / clicks),
                "start": date_window[0].isoformat(),
                "end": date_window[1].isoformat(),
                "records": {experiment.id: experiment.version, **versions},
            }
        )
    return results


def forecast_comparison(db, workspace, experiment, orders, funnels):
    saved = get(db, workspace, experiment.data["forecast_id"], "forecasts")
    data = saved.data
    inputs, base = data["inputs"], next(row for row in data["result"]["scenarios"] if row["name"] == "BASE")
    channel = inputs["channel"]
    all_orders = orders
    orders = [row for row in orders if row.data["channel"] == channel]
    funnels = [row for row in funnels if row.data["channel"] == channel]
    start, end = (iso(experiment.data[key]).astimezone(BEIJING) for key in ("start_at", "end_at"))
    reasons, _ = reconcile_channel(db, workspace, orders, funnels, required_days(start.date(), end.date()))
    groups, matured, provisional, incomplete = summarize_orders(orders)
    expired = now() >= end
    if not expired:
        reasons.append("预测窗口尚未结束")
    if incomplete or provisional:
        reasons.append("部分订单未成熟、未结算或费用不全")
    known_ads = total_complete(
        [D(row.data["ad_spend"]) if row.data.get("ad_spend") is not None else None for row in funnels]
    )
    if known_ads is None:
        reasons.append("广告费待补")
    allocated_ads = sum(
        (D(row.data["advertising"]) for row in orders if row.data.get("advertising") is not None), D(0)
    )
    if known_ads is not None and allocated_ads != known_ads:
        reasons.append("账本广告费与渠道支出未对齐")
    if any(row.data["channel"] != channel for row in all_orders if order_key(row) in groups):
        reasons.append("订单跨渠道归属冲突")
    complete = not reasons
    paid, refunds = (
        sum((D(row.data[key]) for row in orders), D(0)) for key in ("revenue", "customer_refund")
    )
    contribution = sum((value for values in matured for _, value in values), D(0))
    clicks = total_complete([row.data.get("clicks") for row in funnels])
    is_clicks = inputs["traffic"]["denominator"] == "CLICKS"
    metrics = []

    def add(key, expected, actual, *, comparable=True, unit="money"):
        if unit == "count":
            fmt = int
        elif unit == "ratio":
            fmt = float
        else:
            fmt = money
        metrics.append(
            {
                "key": key,
                "unit": unit,
                "forecast": fmt(expected) if expected is not None else None,
                "actual": fmt(actual) if actual is not None else None,
                "difference": fmt(D(str(actual)) - D(str(expected)))
                if complete and comparable and expected is not None and actual is not None
                else None,
            }
        )

    add(
        "traffic",
        inputs["traffic"]["days"] * inputs["traffic"]["daily_visits"],
        clicks if is_clicks else None,
        unit="count",
    )
    add("conversion", base["conversion"], ratio(len(groups), clicks) if is_clicks else None, unit="ratio")
    add("orders", base["orders"], len(groups), unit="count")
    add("avg_price", data["result"]["effective_price"], ratio(paid, len(groups)))
    add("paid_revenue", base["paid_revenue"], paid)
    add("net_revenue", base["net_revenue"], paid - refunds)
    add(
        "refunds",
        D(base["paid_revenue"]) - D(base["net_revenue"]) if base["net_revenue"] is not None else None,
        refunds,
    )
    add("advertising", base["advertising_cost"], known_ads)
    purchase_net = sum(
        (D(row.data["purchase"]) - D(row.data["purchase_refund"]) for values in matured for row, _ in values),
        D(0),
    )
    add("purchase_unit", inputs["c"], ratio(purchase_net, len(matured)), comparable=False)
    add("contribution", base["contribution"], contribution if matured or complete else None)
    return {
        "forecast_id": saved.id,
        "name": data["name"],
        "version": data["plan_version"],
        "channel": channel,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "state": "RECONCILED" if complete else "PENDING",
        "missing": reasons,
        "coverage": float(ratio(len(matured), len(groups))) if groups else None,
        "comparable_traffic": is_clicks,
        "metrics": metrics,
    }


def experiment_summary(db, workspace, experiment_id):
    experiment = get(db, workspace, experiment_id, "experiments")
    candidate = get(db, workspace, experiment.data["candidate_id"], "candidates")
    orders = experiment_orders(db, workspace, experiment)
    funnel = experiment_funnels(db, workspace, experiment)
    groups, matured, provisional, incomplete = summarize_orders(orders)
    contribution = sum((value for values in matured for _, value in values), D(0))
    known_spend = sum(
        (D(row.data["ad_spend"]) for row in funnel if row.data.get("ad_spend") is not None), D(0)
    )
    recorded = {
        (row.data["business_date"], row.data["channel"])
        for row in funnel
        if row.data.get("ad_spend") is not None
    }
    order_sources = {(paid_day(row).isoformat(), row.data["channel"]) for row in orders}
    spend_missing = (
        not funnel or any(row.data.get("ad_spend") is None for row in funnel) or not order_sources <= recorded
    )
    if experiment.data.get("forecast_id"):
        saved = get(db, workspace, experiment.data["forecast_id"], "forecasts")
        start, end = (iso(experiment.data[key]).astimezone(BEIJING).date() for key in ("start_at", "end_at"))
        before = min(end, now().astimezone(BEIJING).date() + timedelta(days=1))
        spend_missing |= any(
            (day, saved.data["inputs"]["channel"]) not in recorded for day in required_days(start, before)
        )
    clicks, impressions, paid_orders = (
        total_complete([row.data.get(key) for row in funnel])
        for key in ("clicks", "impressions", "paid_orders")
    )
    remaining = D(experiment.data["budget"]) - known_spend
    validation = gate(db, workspace, candidate)
    pause_reasons = []
    if remaining <= 0:
        pause_reasons.append("测款广告预算已用完")
    if contribution < -D(experiment.data["loss_limit"]):
        pause_reasons.append("成熟订单亏损超过止损线")
    if validation["gate"] != "PASS":
        pause_reasons.append("审核或证据需要重新核验")
    comparison = (
        forecast_comparison(db, workspace, experiment, orders, funnel)
        if experiment.data.get("forecast_id")
        else None
    )
    if pause_reasons:
        recommendation = "PAUSE"
    elif (
        spend_missing
        or clicks is None
        or clicks < experiment.data["target_clicks"]
        or not matured
        or incomplete
        or provisional
        or (comparison and comparison["state"] != "RECONCILED")
    ):
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
        "ctr": float(ratio(clicks, impressions)) if clicks is not None and impressions else None,
        "conversion": float(ratio(paid_orders, clicks)) if paid_orders is not None and clicks else None,
        "ad_spend": money(known_spend) if not spend_missing else None,
        "known_ad_spend": money(known_spend),
        "remaining_budget": money(remaining) if not spend_missing else None,
        "expired": iso(experiment.data["end_at"]) <= now(),
        "mature_orders": len(matured),
        "provisional_orders": len(provisional),
        "incomplete_orders": len(incomplete),
        "ledger_orders": len(groups),
        "maturity_coverage": float(ratio(len(matured), len(groups))) if groups else None,
        "mature_contribution": money(contribution),
        "mature_per_order": money(contribution / len(matured)) if matured else None,
        "provisional_contribution": money(
            sum((value for values in provisional for _, value in values), D(0))
        ),
        "recommendation": recommendation,
        "pause_reasons": pause_reasons,
        "missing_costs": [
            {"id": row.id, "fields": [key for key in COST_FIELDS if row.data.get(key) is None]}
            for incomplete_order in incomplete
            for row in incomplete_order
        ],
        "forecast_comparison": comparison,
    }


def allocate_ads(db, workspace, experiment_id):
    experiment = get(db, workspace, experiment_id, "experiments")
    orders = experiment_orders(db, workspace, experiment)
    result = []
    for funnel in experiment_funnels(db, workspace, experiment):
        if funnel.data.get("ad_spend") is None:
            continue
        matching = [
            o
            for o in orders
            if o.data["channel"] == funnel.data["channel"]
            and paid_day(o).isoformat() == funnel.data["business_date"]
        ]
        # Do not silently spread spend across a partial order set.
        grouped = group_orders(matching)
        if funnel.data.get("paid_orders") != len(grouped):
            raise BusinessError(
                "ORDER_COVERAGE",
                "渠道支付订单数与已导入订单不一致，不能分摊",
                409,
                {"date": funnel.data["business_date"], "channel": funnel.data["channel"]},
            )
        if not matching and D(funnel.data["ad_spend"]) != 0:
            raise BusinessError("NO_PAID_ORDERS", "无支付订单，广告费保留为未分摊经营支出", 409)
        allocation = distribute(funnel.data["ad_spend"], grouped)
        for key, amount in allocation.items():
            lines = grouped[key]
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
    groups, mature, _, _ = summarize_orders(orders)
    totals = sum((value for paid in mature for _, value in paid), D(0))
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
        "unsettled_orders": sum(any(not row.data["settled"] for row in paid) for paid in groups.values()),
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
