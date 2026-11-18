from collections import defaultdict

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.orm import aliased

from app.db import Audit, Entity, Link, Revision, now
from app.domain import fingerprint, iso, order_contribution, profit
from app.errors import BusinessError
from app.schemas import CONTRACTS, REFERENCES

IMMUTABLE = {"scores", "strategies", "profits", "assessments", "risks", "samples", "forecasts"}
NATURAL_KEYS = {
    "sources": ["name"],
    "products": ["category", "name", "brand"],
    "skus": ["product_id", "spec_key"],
    "listings": ["platform", "external_id", "external_sku_id"],
    "matches": ["listing_id", "sku_id"],
    "suppliers": ["platform", "external_id"],
    "evidence": ["source_id", "locator", "observed_at"],
    "keywords": ["source_id", "category", "keyword", "window_start", "window_end", "metric_definition"],
    "prices": ["listing_id", "observed_at", "price_kind", "region", "currency", "quantity"],
    "offers": ["supplier_id", "sku_id", "evidence_id", "region", "quantity"],
    "candidates": ["sku_id"],
    "strategies": ["version"],
    "funnels": ["experiment_id", "business_date", "channel"],
    "orders": ["source_id", "external_order_id", "line_id"],
    "transactions": ["source_id", "source_row"],
    "signals": ["listing_id", "metric_kind", "unit", "observed_at", "window_start", "window_end"],
}


class ReadSnapshot:
    """Request-local records for reusing the same validation rules on a bulk read."""

    def __init__(self, workspace, entities):
        self.workspace = workspace
        self.evidence_checks = {}
        self.by_id = {row.id: row for row in entities if row.workspace == workspace}
        self.by_kind = defaultdict(list)
        self.by_relation = defaultdict(list)
        for row in sorted(self.by_id.values(), key=lambda r: (-r.updated_at.timestamp(), r.id)):
            self.by_kind[row.kind].append(row)
            for field in ("candidate_id", "offer_id", "listing_id", "sku_id"):
                if row.data.get(field):
                    self.by_relation[row.kind, field, row.data[field]].append(row)

    def find(self, workspace, kind, filters):
        if workspace != self.workspace:
            return []
        pool = self.by_kind[kind]
        for field in ("candidate_id", "offer_id", "listing_id", "sku_id"):
            if field in filters:
                pool = self.by_relation[kind, field, filters[field]]
                if len(filters) == 1:
                    return pool
                break
        return [row for row in pool if all(row.data.get(field) == val for field, val in filters.items())]


def row_out(row):
    return {
        "id": row.id,
        "workspace": row.workspace,
        "kind": row.kind,
        "version": row.version,
        "data": row.data,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def display_label(db, workspace, row):
    d = row.data
    if row.kind == "candidates":
        return get(db, workspace, d["sku_id"]).data["name"]
    if row.kind == "offers":
        return f"{get(db, workspace, d['sku_id']).data['name']} / {get(db, workspace, d['supplier_id']).data['name']} · ¥{d['unit_cost']}"
    if row.kind == "listings":
        return f"{d['platform']} · {d['title']} · {d['external_id']}"
    if row.kind == "experiments":
        candidate = get(db, workspace, d["candidate_id"])
        return display_label(db, workspace, candidate) + " · " + d["hypothesis"]
    return None


def apply_search(stmt, workspace, search):
    if not search:
        return stmt
    needle = "%" + search.replace("%", "\\%").replace("_", "\\_") + "%"
    reference = aliased(Entity)
    linked = (
        select(Link.from_id)
        .join(reference, Link.to_id == reference.id)
        .where(
            Link.workspace == workspace,
            reference.workspace == workspace,
            cast(reference.data, String).ilike(needle),
        )
    )
    return stmt.where(or_(cast(Entity.data, String).ilike(needle), Entity.id.in_(linked)))


def get(db, workspace, entity_id, kind=None):
    if isinstance(db, ReadSnapshot):
        row = db.by_id.get(entity_id) if workspace == db.workspace else None
    else:
        row = db.scalar(select(Entity).where(Entity.id == entity_id, Entity.workspace == workspace))
    if row is None or (kind and row.kind != kind):
        raise BusinessError("NOT_FOUND", "记录不存在或不属于当前数据空间", 404)
    return row


def query(workspace, kind, **filters):
    stmt = select(Entity).where(Entity.workspace == workspace, Entity.kind == kind)
    for key, value in filters.items():
        if value is not None:
            stmt = stmt.where(Entity.data[key].as_string() == str(value))
    return stmt


def rows(db, workspace, kind, **filters):
    if isinstance(db, ReadSnapshot):
        return db.find(workspace, kind, filters)
    return list(db.scalars(query(workspace, kind, **filters).order_by(Entity.updated_at.desc(), Entity.id)))


def latest(db, workspace, kind, **filters):
    if isinstance(db, ReadSnapshot):
        return next(iter(db.find(workspace, kind, filters)), None)
    return db.scalar(query(workspace, kind, **filters).order_by(Entity.updated_at.desc(), Entity.id).limit(1))


def require_source(db, workspace, source_id, kind=None):
    source = get(db, workspace, source_id, "sources")
    if not source.data["enabled"] or (
        source.data.get("expires_at") and iso(source.data["expires_at"]) <= now()
    ):
        raise BusinessError("SOURCE_NOT_AUTHORIZED", "数据来源已停用或授权已过期")
    if kind and kind not in source.data["permission_scope"] and "*" not in source.data["permission_scope"]:
        raise BusinessError("SOURCE_NOT_AUTHORIZED", "来源授权不包含此类数据")
    return source


def require_evidence(db, workspace, evidence_id, kind=None, expected_version=None):
    cache_key = (evidence_id, kind, expected_version)
    scoped = isinstance(db, ReadSnapshot) and db.workspace == workspace
    if scoped and cache_key in db.evidence_checks:
        return db.evidence_checks[cache_key]
    evidence = get(db, workspace, evidence_id, "evidence")
    require_source(db, workspace, evidence.data["source_id"], kind)
    if evidence.data["quality_flag"] != "valid":
        raise BusinessError("EVIDENCE_INVALID", "证据存在冲突或已被拒绝")
    if evidence.data.get("expires_at") and iso(evidence.data["expires_at"]) <= now():
        raise BusinessError("EVIDENCE_STALE", "证据已过期")
    if expected_version is not None and expected_version != evidence.version:
        raise BusinessError("EVIDENCE_REVISED", "证据内容已修订，需要重新核验")
    if evidence.data.get("market_report_id"):
        from app.market_service import require_report_current

        require_report_current(
            db, workspace, get(db, workspace, evidence.data["market_report_id"], "market_reports")
        )
    if scoped:
        db.evidence_checks[cache_key] = evidence
    return evidence


def audit(db, workspace, entity_id, action, details=None):
    db.add(Audit(workspace=workspace, entity_id=entity_id, action=action, details=details or {}))


def persist(
    db, workspace, kind, data, *, entity=None, expected_version=None, external_key=None, action="save"
):
    # Validate every relationship and mirror it in scoped relational foreign keys.
    refs = []
    for field, target_kind in REFERENCES.items():
        value = data.get(field)
        if value:
            get(db, workspace, value, target_kind)
            refs.append((field, value))
    if entity:
        if expected_version != entity.version:
            raise BusinessError("VERSION_CONFLICT", "记录已被更新，请刷新后重试", 409)
        version = entity.version + 1
        result = db.execute(
            update(Entity)
            .where(
                Entity.id == entity.id,
                Entity.workspace == workspace,
                Entity.version == expected_version,
            )
            .values(data=data, version=version, updated_at=now())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise BusinessError("VERSION_CONFLICT", "记录已被更新，请刷新后重试", 409)
        db.expire(entity)
        db.refresh(entity)
        db.execute(delete(Link).where(Link.workspace == workspace, Link.from_id == entity.id))
    else:
        entity = Entity(workspace=workspace, kind=kind, data=data, external_key=external_key)
        db.add(entity)
        db.flush()
    for field, value in refs:
        db.add(Link(workspace=workspace, from_id=entity.id, field=field, to_id=value))
    db.add(Revision(workspace=workspace, entity_id=entity.id, version=entity.version, data=data))
    audit(db, workspace, entity.id, action, {"kind": kind, "version": entity.version})
    db.flush()
    return entity


def validate_strategy(data, workspace):
    from app.domain import WEIGHTS

    c = data["config"]
    try:
        weights = c["score"]["weights"]
        if (
            set(weights) != set(WEIGHTS)
            or sum(weights.values()) != 100
            or any(v <= 0 for v in weights.values())
        ):
            raise ValueError("七因子权重必须为正且合计100")
        if workspace != "demo" and c.get("demo_only"):
            raise ValueError("模拟配置只能在演示空间使用")
        if c["score"]["min_percentile_sample"] < 2:
            raise ValueError("百分位样本数至少为2")
        for bounds in c["score"].get("fallback_ranges", {}).values():
            if len(bounds) != 2 or bounds[1] <= bounds[0]:
                raise ValueError("归一化区间无效")
        for section in ["blue_ocean", "growth", "price_gap", "pain_point", "bundle"]:
            if section not in c["strategy"]:
                raise ValueError("缺少策略配置")
        if "grades" not in c["score"] or "freshness" not in c:
            raise ValueError("缺少等级或时效配置")
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessError("INVALID_STRATEGY", str(exc)) from exc


def save(
    db, workspace, kind, payload, *, entity=None, expected_version=None, upsert=False, import_metadata=None
):
    if entity and kind in IMMUTABLE:
        raise BusinessError("IMMUTABLE_RECORD", "此记录不可覆盖，请创建新版本", 409)
    data = CONTRACTS[kind].model_validate(payload).model_dump(mode="json")
    if import_metadata:
        data["_import"] = import_metadata
    if data.get("source_id") and kind != "evidence":
        require_source(db, workspace, data["source_id"], kind)
    if data.get("evidence_id"):
        evidence = require_evidence(db, workspace, data["evidence_id"], kind)
        data["evidence_version"] = evidence.version
        if data.get("source_id") and data["source_id"] != evidence.data["source_id"]:
            raise BusinessError("SOURCE_MISMATCH", "证据与记录的数据来源不一致")
    if kind == "evidence":
        require_source(db, workspace, data["source_id"])
        if iso(data["observed_at"]) > now():
            raise BusinessError("INVALID_TIME", "证据采集时间不能在未来")
    if kind == "strategies":
        validate_strategy(data, workspace)
    if kind == "keywords" and iso(data["window_end"]) > iso(data["observed_at"]):
        raise BusinessError("INVALID_WINDOW", "只接受已完整结束的统计窗口")
    if kind in {"keywords", "prices"} and iso(data["observed_at"]) > now():
        raise BusinessError("INVALID_TIME", "采集时间不能在未来")
    if kind in {"risks", "samples"}:
        filters = (
            {"candidate_id": data["candidate_id"], "risk_code": data["risk_code"]}
            if kind == "risks"
            else {"offer_id": data["offer_id"]}
        )
        previous = latest(db, workspace, kind, **filters)
        if (
            previous
            and previous.data.get("status", previous.data.get("result")) in {"BLOCKED", "FAILED"}
            and data.get("status", data.get("result")) in {"PASS", "PASSED"}
        ):
            if (
                previous.data["evidence_id"] == data["evidence_id"]
                and previous.data.get("evidence_version", 1) == evidence.version
            ):
                raise BusinessError(
                    "NEW_EVIDENCE_REQUIRED", "解除阻断必须提供新的证据或已修订的证据版本", 409
                )
    if kind in {"candidates", "offers", "prices", "matches"}:
        from app.workflows import validate_resource

        validate_resource(db, workspace, kind, data, entity)
    if kind == "candidates":
        data["state"] = entity.data["state"] if entity else "DISCOVERED"
    if kind == "matches":
        data["state"] = "PENDING"
    if kind == "profits":
        data["result"] = profit(data)
        candidate = get(db, workspace, data["candidate_id"], "candidates")
        offer_id = candidate.data.get("primary_offer_id")
        offer = get(db, workspace, offer_id, "offers") if offer_id else None
        data["offer_version"] = {"id": offer.id, "version": offer.version} if offer else None
        data["procurement_mode"] = candidate.data.get("procurement_mode", "DROPSHIP")
    if kind == "orders":
        get(db, workspace, data["experiment_id"], "experiments")
        if not data["external_order_id"].startswith("sha256:"):
            data["external_order_id"] = "sha256:" + fingerprint(
                [workspace, data["source_id"], data["external_order_id"]]
            )
        computed = order_contribution(data)
        data["contribution"] = format(computed, "f") if computed is not None else None
        data["cost_complete"] = computed is not None
    if kind == "transactions" and workspace != "public":
        raise BusinessError("HISTORY_SPACE_REQUIRED", "公开历史交易必须导入公开历史空间")
    if kind == "transactions":
        data["cancelled"] = data["invoice_no"].upper().startswith("C") or data["quantity"] < 0
        data["cancellation_status"] = "UNMATCHED" if data["cancelled"] else "SALE"
        data["amount"] = str(data["quantity"] * __import__("decimal").Decimal(data["unit_price"]))
        data["contribution"] = None
    if kind == "experiments":
        from app.workflows import validate_experiment

        validate_experiment(db, workspace, data, entity)
        data["status"] = "PLANNED"
        candidate = get(db, workspace, data["candidate_id"], "candidates")
        score = latest(db, workspace, "scores", candidate_id=candidate.id)
        data["score_run_id"] = score.id if score else None
    fields = NATURAL_KEYS.get(kind)
    natural = fingerprint([kind, *[data.get(k) for k in fields]]) if fields else None
    existing = (
        db.scalar(
            select(Entity).where(
                Entity.workspace == workspace,
                Entity.kind == kind,
                Entity.external_key == natural,
            )
        )
        if natural
        else None
    )
    if existing and entity is None:
        if not upsert:
            raise BusinessError("DUPLICATE_KEY", "该记录已存在，请修改现有记录或创建新的历史快照", 409)
        if kind == "orders" and existing.data["experiment_id"] != data["experiment_id"]:
            raise BusinessError("EXPERIMENT_CONFLICT", "同一订单明细不能重复归属其他测款计划", 409)
        clean = lambda value: {
            k: v for k, v in value.items() if k not in {"evidence_id", "evidence_version", "_import"}
        }
        if clean(existing.data) == clean(data):
            return existing, "unchanged"
        entity, expected_version = existing, existing.version
    if entity and entity.external_key != natural:
        raise BusinessError("KEY_IMMUTABLE", "记录的业务标识不可修改，请创建新记录", 409)
    result = persist(
        db, workspace, kind, data, entity=entity, expected_version=expected_version, external_key=natural
    )
    return result, "updated" if entity else "created"


def list_page(db, workspace, kind, page=1, page_size=20, search="", filters=None):
    stmt = query(workspace, kind, **(filters or {}))
    stmt = apply_search(stmt, workspace, search)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Entity.created_at.desc(), Entity.id).offset((page - 1) * page_size).limit(page_size)
        )
    )
    output = []
    for row in items:
        result = row_out(row)
        label = display_label(db, workspace, row)
        if label:
            result["data"] = {**result["data"], "_label": label}
        if kind == "candidates":
            from app.selection import score_stale
            from app.workflows import gate

            score = latest(db, workspace, "scores", candidate_id=row.id)
            normal = latest(db, workspace, "profits", candidate_id=row.id, label="NORMAL")
            requirements = gate(db, workspace, row)
            summary = {
                "gate": requirements["gate"],
                "score_stale": score_stale(db, workspace, score, row, requirements),
                "score": score.data.get("score") if score else None,
                "coverage": score.data.get("coverage") if score else None,
                "grade": score.data.get("grade") if score else None,
                "contribution": profit(normal.data)["contribution"] if normal else None,
            }
            result["data"] = {**result["data"], "_summary": summary}
        output.append(result)
    return {"items": output, "total": total, "page": page, "page_size": page_size}
