from datetime import timedelta
from decimal import Decimal as D

from app.db import now
from app.domain import iso, match_conflicts, money, profit, quantile
from app.errors import BusinessError
from app.store import audit, get, latest, persist, require_evidence, rows

TRANSITIONS = {
    "DISCOVERED": {"RESEARCHING", "HOLD", "REJECTED"},
    "RESEARCHING": {"READY_SAMPLE", "HOLD", "REJECTED"},
    "READY_SAMPLE": {"SAMPLE_PASSED", "RESEARCHING", "HOLD", "REJECTED"},
    "SAMPLE_PASSED": {"READY_TEST", "RESEARCHING", "HOLD", "REJECTED"},
    "READY_TEST": {"TESTING", "RESEARCHING", "HOLD", "REJECTED"},
    "TESTING": {"SCALE", "HOLD", "REJECTED"},
    "SCALE": {"HOLD", "REJECTED"},
    "HOLD": {"RESEARCHING", "REJECTED", "ARCHIVED"},
    "REJECTED": {"RESEARCHING", "ARCHIVED"},
    "ARCHIVED": set(),
}


def config_for(db, workspace):
    row = latest(db, workspace, "strategies")
    if row is None:
        raise BusinessError("NO_STRATEGY", "请先创建策略配置")
    return row


def validate_resource(db, workspace, kind, data, entity):
    if kind == "candidates":
        sku = get(db, workspace, data["sku_id"], "skus")
        if data.get("keyword_id"):
            keyword = get(db, workspace, data["keyword_id"], "keywords")
            product = get(db, workspace, sku.data["product_id"], "products")
            if keyword.data["category"] != product.data["category"]:
                raise BusinessError("CATEGORY_CONFLICT", "关联关键词与商品类目不一致")
        primary, backup = data.get("primary_offer_id"), data.get("backup_offer_id")
        for offer_id in [primary, backup]:
            if offer_id and get(db, workspace, offer_id, "offers").data["sku_id"] != sku.id:
                raise BusinessError("SKU_CONFLICT", "候选与供应商报价必须属于同一SKU")
        if primary and backup:
            a, b = get(db, workspace, primary), get(db, workspace, backup)
            if a.data["supplier_id"] == b.data["supplier_id"]:
                raise BusinessError("BACKUP_CONFLICT", "主备供货必须来自不同供应商")
        if entity and entity.data["state"] in {"READY_TEST", "TESTING", "SCALE"}:
            changed = any(
                data.get(k) != entity.data.get(k)
                for k in ["sku_id", "primary_offer_id", "backup_offer_id", "keyword_id"]
            )
            if changed:
                raise BusinessError("RESEARCH_REQUIRED", "更换研究依据或货源前，请先暂停并返回研究状态", 409)
    if kind == "offers":
        sku = get(db, workspace, data["sku_id"], "skus")
        if sku.data["quantity"] != data["quantity"]:
            raise BusinessError("SKU_CONFLICT", "报价包装数量与标准SKU不一致")
    if kind == "prices":
        listing = get(db, workspace, data["listing_id"], "listings")
        if listing.data["quantity"] != data["quantity"]:
            raise BusinessError("SKU_CONFLICT", "价格包装数量与平台SKU不一致")


def review_match(db, workspace, match_id, review):
    row = get(db, workspace, match_id, "matches")
    evidence = require_evidence(db, workspace, review.evidence_id, "matches")
    sku, listing = get(db, workspace, row.data["sku_id"]), get(db, workspace, row.data["listing_id"])
    required = config_for(db, workspace).data["config"].get("required_attributes", ["material", "size"])
    conflicts, missing = match_conflicts(sku.data, listing.data, required)
    if review.state == "MATCHED" and (conflicts or missing):
        raise BusinessError(
            "SKU_CONFLICT",
            "规格冲突或必要属性缺失，不能确认同款",
            409,
            {"conflicts": conflicts, "missing": missing},
        )
    if review.state == "MATCHED":
        other = [
            m
            for m in rows(db, workspace, "matches", listing_id=listing.id)
            if m.id != row.id and m.data.get("state") == "MATCHED"
        ]
        if other:
            raise BusinessError("ALREADY_MATCHED", "该平台SKU已确认至另一标准SKU", 409)
    data = {
        **row.data,
        "state": review.state,
        "evidence_id": review.evidence_id,
        "evidence_version": evidence.version,
        "reason": review.reason,
        "reviewed_at": now().isoformat(),
        "reviewed_by": "owner",
        "sku_version": sku.version,
        "listing_version": listing.version,
    }
    return persist(
        db,
        workspace,
        "matches",
        data,
        entity=row,
        expected_version=review.expected_version,
        action="review_match",
    )


def comparisons(db, workspace, sku_id, region="全国", currency="CNY"):
    sku = get(db, workspace, sku_id, "skus")
    config = config_for(db, workspace).data["config"]
    included, excluded = [], []
    # One most-recent quote per listing and comparable price basis, never count history twice.
    for match in rows(db, workspace, "matches", sku_id=sku_id):
        listing = get(db, workspace, match.data["listing_id"])
        reason = None
        if match.data.get("state") != "MATCHED":
            reason = "同款尚未确认"
        elif (
            match.data.get("sku_version") != sku.version
            or match.data.get("listing_version") != listing.version
        ):
            reason = "规格已更新，需要重新确认"
        else:
            try:
                require_evidence(
                    db, workspace, match.data["evidence_id"], "matches", match.data.get("evidence_version")
                )
            except BusinessError as exc:
                reason = exc.message
        prices = sorted(
            rows(db, workspace, "prices", listing_id=listing.id),
            key=lambda r: iso(r.data["observed_at"]),
            reverse=True,
        )
        # Group per price kind before eligibility checks, so an expired latest quote cannot resurrect older data.
        seen = set()
        eligible = []
        for price in prices:
            p = price.data
            if p["region"] != region or p["currency"] != currency or p["quantity"] != sku.data["quantity"]:
                continue
            basis = p["price_kind"]
            if basis in seen:
                continue
            seen.add(basis)
            why = reason
            if p["price_kind"] not in {"LIST", "ELIGIBLE"}:
                why = "采购/批发价不计入零售价格带"
            elif p["price_kind"] == "ELIGIBLE" and not p["eligibility_verified"]:
                why = "优惠资格未经核验"
            elif p["shipping"] is None:
                why = "运费未知"
            elif iso(p["observed_at"]) < now() - timedelta(hours=config["freshness"]["price_hours"]):
                why = "价格已过期"
            try:
                require_evidence(db, workspace, p["evidence_id"], "prices", p.get("evidence_version"))
            except BusinessError as exc:
                why = exc.message
            item = {
                "id": price.id,
                "listing_id": listing.id,
                "platform": listing.data["platform"],
                "title": listing.data["title"],
                "price_kind": p["price_kind"],
                "data": p,
            }
            if why:
                excluded.append({**item, "reason": why})
            else:
                eligible.append({**item, "total": money(D(p["price"]) + D(p["shipping"]))})
        if eligible:
            included.append(min(eligible, key=lambda x: D(x["total"])))
        elif not prices:
            excluded.append(
                {"listing_id": listing.id, "title": listing.data["title"], "reason": reason or "尚无报价"}
            )
    values = [D(x["total"]) for x in included]
    stats = {"count": len(values), "sparse": len(values) < 5, "region": region, "currency": currency}
    if values:
        stats.update(
            {
                "min": money(min(values)),
                "p25": money(quantile(values, ".25")),
                "median": money(quantile(values, ".5")),
                "p75": money(quantile(values, ".75")),
                "max": money(max(values)),
            }
        )
    return {"included": included, "excluded": excluded, "statistics": stats}


def gate(db, workspace, candidate, stage="READY_TEST"):
    missing, blocked = [], []
    refs = []

    def fresh(row, label, kind):
        refs.append(row)
        try:
            require_evidence(db, workspace, row.data["evidence_id"], kind, row.data.get("evidence_version"))
        except BusinessError:
            missing.append(label + "的有效证据")

    risks = rows(db, workspace, "risks", candidate_id=candidate.id)
    newest = {}
    for risk in risks:
        newest.setdefault(risk.data["risk_code"], risk)
    for code, risk in newest.items():
        refs.append(risk)
        if risk.data["status"] == "BLOCKED":
            blocked.append(code + ": " + risk.data["reason"])
    if stage in {"READY_TEST", "TESTING", "SCALE"}:
        if candidate.data.get("keyword_id"):
            keyword = get(db, workspace, candidate.data["keyword_id"], "keywords")
            fresh(keyword, "关键词", "keywords")
            hours = config_for(db, workspace).data["config"]["freshness"]["keyword_snapshot_hours"]
            if iso(keyword.data["observed_at"]) < now() - timedelta(hours=hours):
                missing.append("关键词快照已过期")
        for code in ["IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"]:
            risk = newest.get(code)
            if not risk or risk.data["status"] != "PASS":
                missing.append(code + "审核")
            elif risk:
                fresh(risk, code + "审核", "risks")
    offers = []
    for field, label in [("primary_offer_id", "主供"), ("backup_offer_id", "备供")]:
        offer_id = candidate.data.get(field)
        if field == "backup_offer_id" and stage in {"READY_SAMPLE", "SAMPLE_PASSED"}:
            continue
        if not offer_id:
            missing.append(label + "报价")
            continue
        offer = get(db, workspace, offer_id, "offers")
        offers.append(offer)
        fresh(offer, label, "offers")
        d = offer.data
        if not d["verified"] or not d["dropship"] or d["shipping"] is None or d["service_fee"] is None:
            missing.append(label + "代发、费用或核验信息")
        if iso(d["expires_at"]) <= now():
            missing.append(label + "有效报价")
        sample = latest(db, workspace, "samples", offer_id=offer.id)
        if sample and sample.data["result"] == "FAILED":
            blocked.append(label + "样品失败")
        if stage != "READY_SAMPLE":
            if not sample or sample.data["result"] != "PASSED":
                missing.append(label + "样品合格记录")
            else:
                fresh(sample, label + "样品", "samples")
    if len(offers) == 2 and offers[0].data["supplier_id"] == offers[1].data["supplier_id"]:
        blocked.append("主备供应商相同")
    normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
    stress = latest(db, workspace, "profits", candidate_id=candidate.id, label="STRESS")
    if stress and D(profit(stress.data)["contribution"]) < 0:
        blocked.append("压力情景贡献为负")
    if stage in {"READY_TEST", "TESTING", "SCALE"}:
        for scenario, label in [(normal, "正常"), (stress, "压力")]:
            if not scenario:
                missing.append(label + "利润情景")
                continue
            fresh(scenario, label + "利润", "profits")
            primary = offers[0] if offers and candidate.data.get("primary_offer_id") == offers[0].id else None
            if primary and scenario.data.get("offer_version") != {
                "id": primary.id,
                "version": primary.version,
            }:
                missing.append(label + "利润需按最新主供报价重算")
            if primary and label == "正常":
                p = primary.data
                if (
                    D(scenario.data["c"]) != D(p["unit_cost"])
                    or p["shipping"] is None
                    or D(scenario.data["f"]) != D(p["shipping"])
                ):
                    missing.append("正常情景采购及运费与主供报价不一致")
                if p["service_fee"] is not None and D(scenario.data["b"]) < D(p["service_fee"]):
                    missing.append("正常情景遗漏代发服务费")
        if normal and stress:
            n, s = normal.data, stress.data
            adverse = D(s["p"]) <= D(n["p"]) and all(
                D(s[k]) >= D(n[k]) for k in ["c", "f", "b", "t", "a", "r", "l"]
            )
            strict = D(s["p"]) < D(n["p"]) or any(
                D(s[k]) > D(n[k]) for k in ["c", "f", "b", "t", "a", "r", "l"]
            )
            if not adverse or not strict:
                missing.append("压力情景必须包含至少一项不利变化且不得改善其他参数")
        if normal and D(profit(normal.data)["contribution"]) <= 0:
            blocked.append("正常情景贡献非正")
    status = "BLOCKED" if blocked else "NEEDS_EVIDENCE" if missing else "PASS"
    return {
        "gate": status,
        "missing": sorted(set(missing)),
        "blocked": sorted(set(blocked)),
        "references": [{"id": r.id, "version": r.version} for r in refs],
    }


def validate_experiment(db, workspace, data):
    if workspace == "public":
        raise BusinessError("HISTORICAL_ONLY", "公开历史数据用于离线研究，不能启动经营测款", 409)
    candidate = get(db, workspace, data["candidate_id"], "candidates")
    result = gate(db, workspace, candidate)
    if candidate.data["state"] != "READY_TEST" or result["gate"] != "PASS":
        raise BusinessError("GATE_BLOCKED", "候选尚未完成测款准备", 409, result)
    if any(
        e.data["status"] in {"PLANNED", "RUNNING"}
        for e in rows(db, workspace, "experiments", candidate_id=candidate.id)
    ):
        raise BusinessError("EXPERIMENT_EXISTS", "已有未结束的测款计划", 409)


def transition(db, workspace, candidate_id, request):
    candidate = get(db, workspace, candidate_id, "candidates")
    target = request.target_state
    if target not in TRANSITIONS[candidate.data["state"]]:
        raise BusinessError("INVALID_TRANSITION", "不允许此状态迁移", 409)
    for evidence_id in request.evidence_ids:
        require_evidence(db, workspace, evidence_id)
    if target in {"READY_SAMPLE", "SAMPLE_PASSED", "READY_TEST", "TESTING", "SCALE"}:
        result = gate(db, workspace, candidate, target)
        if result["gate"] != "PASS":
            raise BusinessError("GATE_BLOCKED", "审核条件未满足", 409, result)
    if target == "TESTING":
        experiment = latest(db, workspace, "experiments", candidate_id=candidate.id)
        if not experiment or experiment.data["status"] != "PLANNED":
            raise BusinessError("EXPERIMENT_REQUIRED", "请先建立测款计划", 409)
        persist(
            db,
            workspace,
            "experiments",
            {**experiment.data, "status": "RUNNING"},
            entity=experiment,
            expected_version=experiment.version,
            action="start_experiment",
        )
    if target == "SCALE":
        from app.reporting import experiment_summary

        experiment = latest(db, workspace, "experiments", candidate_id=candidate.id)
        summary = experiment_summary(db, workspace, experiment.id) if experiment else {}
        if (
            not summary.get("mature_orders")
            or D(summary["mature_contribution"]) <= 0
            or summary["recommendation"] != "REVIEW_SCALE"
        ):
            raise BusinessError("INSUFFICIENT_RESULTS", "成熟贡献、预算或观察样本尚不支持扩大", 409, summary)
    if target in {"HOLD", "REJECTED", "SCALE"}:
        for experiment in rows(db, workspace, "experiments", candidate_id=candidate.id):
            if experiment.data["status"] in {"RUNNING", "PLANNED"}:
                persist(
                    db,
                    workspace,
                    "experiments",
                    {**experiment.data, "status": "PAUSED" if target == "HOLD" else "CLOSED"},
                    entity=experiment,
                    expected_version=experiment.version,
                    action="finish_experiment",
                )
    updated = persist(
        db,
        workspace,
        "candidates",
        {**candidate.data, "state": target},
        entity=candidate,
        expected_version=request.expected_version,
        action="transition",
    )
    audit(
        db,
        workspace,
        candidate.id,
        "transition_reason",
        {"reason": request.reason, "evidence_ids": request.evidence_ids},
    )
    return updated
