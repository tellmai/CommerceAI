from datetime import timedelta
from decimal import Decimal as D

from app.db import Entity, now
from app.domain import (
    blue_ocean,
    check,
    decimal,
    fingerprint,
    growth,
    iso,
    normalize,
    profit,
    ratio,
    summarize_scores,
    tri_all,
    tri_any,
    weighted,
)
from app.errors import BusinessError
from app.store import get, latest, persist, query, require_evidence, rows
from app.workflows import comparisons, config_for, gate


def keyword_analysis(db, workspace, row, config):
    k = row.data
    history = rows(
        db,
        workspace,
        "keywords",
        keyword=k["keyword"],
        category=k["category"],
        source_id=k["source_id"],
        metric_definition=k["metric_definition"],
    )
    usable = []
    for entry in history:
        try:
            require_evidence(db, workspace, entry.data["evidence_id"], "keywords")
            if iso(entry.data["window_end"]) <= iso(entry.data["observed_at"]):
                usable.append(entry)
        except BusinessError:
            pass
    groups = {}
    for entry in usable:
        duration = iso(entry.data["window_end"]) - iso(entry.data["window_start"])
        groups.setdefault(duration, []).append(entry)
    result = {
        "opportunity": ratio(k.get("search_index"), k.get("listing_count")),
        "growth_7d": None,
        "growth_30d": None,
        "supply_growth": None,
        "comparable_weeks": 0,
    }
    for days, name in [(7, "growth_7d"), (30, "growth_30d")]:
        series = sorted(
            groups.get(timedelta(days=days), []), key=lambda e: iso(e.data["window_end"]), reverse=True
        )
        if not series:
            continue
        current = series[0]
        chain = [current]
        while True:
            previous = next(
                (e for e in series if iso(e.data["window_end"]) == iso(chain[-1].data["window_start"])), None
            )
            if previous is None:
                break
            chain.append(previous)
        if len(chain) >= 2:
            result[name] = growth(chain[0].data.get("search_index"), chain[1].data.get("search_index"))
            if days == 7:
                result["supply_growth"] = growth(
                    chain[0].data.get("listing_count"), chain[1].data.get("listing_count")
                )
        if days == 7:
            result["comparable_weeks"] = len(chain)
    result["growth_gap"] = (
        result["growth_7d"] - result["supply_growth"]
        if result["growth_7d"] is not None and result["supply_growth"] is not None
        else None
    )
    strategies = {"blue_ocean": blue_ocean(k, config)}
    gc = config["strategy"]["growth"]
    strategies["growth"] = tri_all(
        [
            check(result["growth_7d"], lambda x: x > D(str(gc["min_growth_exclusive"]))),
            check(result["growth_30d"], lambda x: x > 0),
            check(result["growth_gap"], lambda x: x > 0),
            "PASS" if result["comparable_weeks"] >= gc["min_comparable_weeks"] else "UNKNOWN",
        ]
    )
    try:
        require_evidence(db, workspace, k["evidence_id"], "keywords")
        fresh = iso(k["observed_at"]) >= now() - timedelta(
            hours=config["freshness"]["keyword_snapshot_hours"]
        )
    except BusinessError:
        fresh = False
    if not fresh:
        strategies = dict.fromkeys(strategies, "UNKNOWN")
    return {
        "metrics": result,
        "strategies": strategies,
        "fresh": fresh,
        "history": [{"id": e.id, "version": e.version, "data": e.data} for e in usable],
    }


def compute_score(inputs):
    c, m, a, pops = inputs["config"], inputs["metrics"], inputs["assessment"], inputs["populations"]

    def norm(name, inverse=False):
        return normalize(m.get(name), pops.get(name, []), name, c, inverse)

    scores = {
        "demand": weighted([(norm("search_index"), ".6"), (norm("conversion"), ".4")]),
        "competition": weighted(
            [
                (norm("opportunity"), ".5"),
                (norm("head_share", True), ".3"),
                (norm("mall_click_share", True), ".2"),
            ]
        ),
        "profit": weighted(
            [
                (norm("contribution"), ".6"),
                (norm("contribution_rate"), ".25"),
                (norm("stress_contribution"), ".15"),
            ]
        ),
        "supply": weighted(
            [
                (a.get("fulfillment_score"), ".4"),
                (a.get("quality_score"), ".4"),
                (a.get("backup_score"), ".2"),
            ]
        ),
        "growth": weighted(
            [(norm("growth_7d"), ".5"), (norm("growth_30d"), ".3"), (norm("growth_gap"), ".2")]
        ),
        "aftersale": weighted([(norm("loss_rate", True), ".6"), (a.get("usage_risk_score"), ".4")]),
        "differentiation": decimal(a.get("differentiation_score")),
    }
    # A manual number cannot stand in for a required sample or usable supplier evidence.
    if not inputs["supply_verified"]:
        scores["supply"] = None
    summary = summarize_scores(scores, c, inputs["gate"]["gate"], inputs["critical_missing"])
    summary["missing_factors"] = [k for k, v in scores.items() if v is None]
    summary["confidence"] = (
        "LOW"
        if inputs["critical_missing"]
        or any(
            len(pops.get(k, [])) < c["score"]["min_percentile_sample"]
            for k in ["search_index", "conversion", "contribution"]
        )
        else "HIGH"
    )
    return summary


def run_score(db, workspace, candidate_id, strategy_id=None):
    candidate = get(db, workspace, candidate_id, "candidates")
    strategy = get(db, workspace, strategy_id, "strategies") if strategy_id else config_for(db, workspace)
    config = strategy.data["config"]
    keyword = (
        get(db, workspace, candidate.data["keyword_id"], "keywords")
        if candidate.data.get("keyword_id")
        else None
    )
    normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
    stress = latest(db, workspace, "profits", candidate_id=candidate.id, label="STRESS")
    assessment = latest(db, workspace, "assessments", candidate_id=candidate.id)
    a = assessment.data if assessment else {}
    valid_a = False
    if assessment:
        try:
            require_evidence(db, workspace, a["evidence_id"], "assessments")
            valid_a = True
        except BusinessError:
            a = {}
    metrics, populations, snapshots = {}, {}, []
    critical = keyword is None or not valid_a
    if keyword:
        analysis = keyword_analysis(db, workspace, keyword, config)
        metrics.update(analysis["metrics"])
        critical = critical or not analysis["fresh"]
        metrics.update({k: keyword.data.get(k) for k in ["search_index", "conversion", "mall_click_share"]})
        snapshots.extend(analysis["history"])
        cohort = list(
            db.scalars(
                query(
                    workspace,
                    "keywords",
                    **{
                        k: keyword.data[k]
                        for k in [
                            "category",
                            "source_id",
                            "window_start",
                            "window_end",
                            "metric_definition",
                        ]
                    },
                )
            )
        )
        for other in cohort:
            try:
                require_evidence(db, workspace, other.data["evidence_id"], "keywords")
            except BusinessError:
                continue
            if iso(other.data["observed_at"]) < now() - timedelta(
                hours=config["freshness"]["keyword_snapshot_hours"]
            ):
                continue
            for k in ["search_index", "conversion", "mall_click_share"]:
                populations.setdefault(k, []).append(other.data.get(k))
            populations.setdefault("opportunity", []).append(
                ratio(other.data.get("search_index"), other.data.get("listing_count"))
            )
            analysis_other = keyword_analysis(db, workspace, other, config)
            for metric in ["growth_7d", "growth_30d", "growth_gap"]:
                populations.setdefault(metric, []).append(analysis_other["metrics"].get(metric))
    if a.get("head_share_precision") == "exact" and a.get("head_share_sample_count", 0) >= 10:
        metrics["head_share"] = a.get("head_share")
    for row, metric in [(normal, "contribution"), (stress, "stress_contribution")]:
        if row:
            try:
                require_evidence(db, workspace, row.data["evidence_id"], "profits")
                result = profit(row.data)
                metrics[metric] = result["contribution"]
                if row is normal:
                    metrics["contribution_rate"] = result["contribution_rate"]
                    metrics["loss_rate"] = ratio(D(str(row.data["r"])) * D(row.data["l"]), row.data["p"])
            except BusinessError:
                critical = True
        else:
            critical = True
    # For non-keyword variables, cohorts consist of the latest comparable candidate observations.
    sku = get(db, workspace, candidate.data["sku_id"])
    product = get(db, workspace, sku.data["product_id"])
    for other in rows(db, workspace, "candidates"):
        other_sku = get(db, workspace, other.data["sku_id"])
        other_product = get(db, workspace, other_sku.data["product_id"])
        if other_product.data["category"] != product.data["category"]:
            continue
        if not keyword or not other.data.get("keyword_id"):
            continue
        ok = get(db, workspace, other.data["keyword_id"])
        if any(
            ok.data[k] != keyword.data[k]
            for k in ["source_id", "window_start", "window_end", "metric_definition"]
        ):
            continue
        other_assessment = latest(db, workspace, "assessments", candidate_id=other.id)
        if other_assessment:
            od = other_assessment.data
            try:
                require_evidence(db, workspace, od["evidence_id"], "assessments", od.get("evidence_version"))
                if od.get("head_share_precision") == "exact" and od.get("head_share_sample_count", 0) >= 10:
                    populations.setdefault("head_share", []).append(od.get("head_share"))
            except BusinessError:
                pass
        for label, metric in [("NORMAL", "contribution"), ("STRESS", "stress_contribution")]:
            other_profit = latest(db, workspace, "profits", candidate_id=other.id, label=label)
            if not other_profit:
                continue
            try:
                require_evidence(db, workspace, other_profit.data["evidence_id"], "profits")
            except BusinessError:
                continue
            result = profit(other_profit.data)
            populations.setdefault(metric, []).append(result["contribution"])
            if label == "NORMAL":
                populations.setdefault("contribution_rate", []).append(result["contribution_rate"])
                populations.setdefault("loss_rate", []).append(
                    ratio(D(str(other_profit.data["r"])) * D(other_profit.data["l"]), other_profit.data["p"])
                )
    requirements = gate(db, workspace, candidate)
    supply_verified = not any(
        "样品" in x or "主供" in x or "备供" in x for x in requirements["missing"] + requirements["blocked"]
    )
    for entity in [candidate, strategy, normal, stress, assessment]:
        if entity:
            snapshots.append({"id": entity.id, "version": entity.version, "data": entity.data})
    for reference in requirements["references"]:
        entity = get(db, workspace, reference["id"])
        snapshots.append({"id": entity.id, "version": entity.version, "data": entity.data})
    # Preserve the permission/evidence chain as well as the business inputs.
    captured = {item["id"]: item for item in snapshots}
    for item in list(captured.values()):
        evidence_id = item["data"].get("evidence_id")
        if evidence_id:
            evidence = get(db, workspace, evidence_id, "evidence")
            source = get(db, workspace, evidence.data["source_id"], "sources")
            for entity in [evidence, source]:
                captured[entity.id] = {"id": entity.id, "version": entity.version, "data": entity.data}
    snapshots = list(captured.values())
    inputs = {
        "config": config,
        "metrics": metrics,
        "assessment": a,
        "populations": populations,
        "gate": requirements,
        "critical_missing": critical,
        "supply_verified": supply_verified,
        "record_versions": {item["id"]: item["version"] for item in snapshots},
    }
    # Normalize Decimal values to exact strings for the immutable JSON snapshot.
    import json

    inputs = json.loads(json.dumps(inputs, default=str))
    result = compute_score(inputs)
    data = {
        "candidate_id": candidate.id,
        "strategy_id": strategy.id,
        "algorithm_version": "rules-1.1",
        "input_snapshot": inputs,
        "records": snapshots,
        "input_hash": fingerprint(inputs),
        **result,
    }
    key = fingerprint([candidate.id, strategy.id, data["input_hash"]])
    existing = db.scalar(
        __import__("sqlalchemy")
        .select(Entity)
        .where(Entity.workspace == workspace, Entity.kind == "scores", Entity.external_key == key)
    )
    return existing or persist(db, workspace, "scores", data, external_key=key, action="score")


def score_stale(db, workspace, score, candidate, requirements=None):
    """A historical score remains replayable, but cannot silently look current."""
    if score is None:
        return False
    if score.data["strategy_id"] != config_for(db, workspace).id:
        return True
    for saved in score.data.get("records", []):
        current = get(db, workspace, saved["id"])
        if current.version != saved["version"]:
            # Stage/note edits do not change the numerical research inputs.
            if current.kind == "candidates" and all(
                current.data.get(k) == saved["data"].get(k)
                for k in ["sku_id", "keyword_id", "primary_offer_id", "backup_offer_id"]
            ):
                continue
            return True
        if current.kind == "evidence":
            try:
                require_evidence(db, workspace, current.id)
            except BusinessError:
                return True
    current_gate = requirements or gate(db, workspace, candidate)
    if current_gate != score.data["input_snapshot"]["gate"]:
        return True
    keyword_id = candidate.data.get("keyword_id")
    if keyword_id:
        keyword = get(db, workspace, keyword_id, "keywords")
        if not keyword_analysis(db, workspace, keyword, config_for(db, workspace).data["config"])["fresh"]:
            return True
        # A new comparable observation changes the normalization population.
        if any(
            row.created_at > score.created_at
            for row in rows(
                db,
                workspace,
                "keywords",
                category=keyword.data["category"],
                source_id=keyword.data["source_id"],
                metric_definition=keyword.data["metric_definition"],
                window_start=keyword.data["window_start"],
                window_end=keyword.data["window_end"],
            )
        ):
            return True
    return False


def strategies_for_candidate(db, workspace, candidate_id):
    candidate = get(db, workspace, candidate_id, "candidates")
    config = config_for(db, workspace).data["config"]
    result = {"blue_ocean": "UNKNOWN", "growth": "UNKNOWN"}
    analysis = None
    if candidate.data.get("keyword_id"):
        analysis = keyword_analysis(db, workspace, get(db, workspace, candidate.data["keyword_id"]), config)
        result.update(analysis["strategies"])
    normal = latest(db, workspace, "profits", candidate_id=candidate_id, label="NORMAL")
    stress = latest(db, workspace, "profits", candidate_id=candidate_id, label="STRESS")
    comparable = comparisons(db, workspace, candidate.data["sku_id"])
    c = config["strategy"]["price_gap"]
    result["price_gap"] = tri_all(
        [
            check(
                profit(normal.data)["contribution"] if normal else None,
                lambda x: x >= D(str(c["normal_contribution_min"])),
            ),
            check(
                profit(stress.data)["contribution"] if stress else None,
                lambda x: x >= D(str(c["stress_contribution_min"])),
            ),
            "PASS" if comparable["statistics"]["count"] >= c["min_comparable_retail_quotes"] else "UNKNOWN",
        ]
    )
    assessment = latest(db, workspace, "assessments", candidate_id=candidate_id)
    a = assessment.data if assessment else {}
    if assessment:
        try:
            require_evidence(db, workspace, a["evidence_id"], "assessments")
        except BusinessError:
            a = {}
    truth = lambda key: "UNKNOWN" if a.get(key) is None else "PASS" if a[key] else "FAIL"
    result["pain_point"] = tri_all(
        [
            check(
                a.get("distinct_reviews"),
                lambda x: x >= config["strategy"]["pain_point"]["min_distinct_reviews"],
            ),
            truth("pain_verified"),
            truth("pain_solvable"),
        ]
    )
    result["bundle"] = tri_all(
        [
            truth("bundle_fulfillment_verified"),
            "UNKNOWN"
            if a.get("single_contribution") is None or a.get("bundle_contribution") is None
            else "PASS"
            if D(a["bundle_contribution"]) > D(a["single_contribution"])
            else "FAIL",
        ]
    )
    return {
        "strategies": result,
        "pool_result": tri_any(list(result.values())),
        "keyword_analysis": analysis,
        "comparisons": comparable,
    }
