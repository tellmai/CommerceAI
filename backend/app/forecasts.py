"""Immutable, workspace-scoped forecast plans and their evidence."""

from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.db import Entity
from app.domain import decimal, fingerprint, iso, money
from app.errors import BusinessError
from app.opportunities import ALGORITHM, ForecastInput, baseline, forecast
from app.schemas import Contract, Text
from app.store import get, latest, persist, query, rows
from app.workflows import candidate_mode

BEIJING = ZoneInfo("Asia/Shanghai")


class ForecastSave(Contract):
    name: Text
    inputs: ForecastInput
    parent_forecast_id: str | None = None


def exact_values(value):
    if isinstance(value, BaseModel):
        return {key: exact_values(getattr(value, key)) for key in type(value).model_fields}
    if isinstance(value, list):
        return [exact_values(item) for item in value]
    return str(value) if isinstance(value, Decimal) else value


def beijing_dates(experiment):
    start, end = (iso(experiment[key]).astimezone(BEIJING) for key in ("start_at", "end_at"))
    if start.time() != time.min or end.time() != time.min:
        return None
    return start.date(), end.date()


def traffic_reference(db, workspace, candidate_id):
    from app.reporting import settled_traffic

    get(db, workspace, candidate_id, "candidates")
    results = []
    for experiment in rows(db, workspace, "experiments", candidate_id=candidate_id):
        if experiment.data["status"] != "CLOSED":
            continue
        for reference in settled_traffic(db, workspace, experiment):
            results.append(
                {
                    "experiment_id": experiment.id,
                    "name": experiment.data["hypothesis"],
                    **reference,
                }
            )
        if results:
            break
    return results


def resolve_forecast(db, workspace, body):
    if workspace == "public":
        raise BusinessError("SPACE_MODE_CONFLICT", "请在模拟演示或真实经营空间测算当前商品", 409)
    source = (
        baseline(db, workspace, body.candidate_id, body.report_id, body.region)
        if (body.candidate_id or body.report_id)
        else None
    )
    report = get(db, workspace, body.report_id, "market_reports") if body.report_id else None
    candidate_id = body.candidate_id or (report.data.get("candidate_id") if report else None)
    if source and body.procurement_mode and body.procurement_mode != source.procurement_mode:
        raise BusinessError("PROCUREMENT_CONFLICT", "请选用与采购方式一致的候选或报价报告")
    body = body.model_copy(
        update={
            "procurement_mode": body.procurement_mode or (source.procurement_mode if source else None),
            "region": body.region or (source.region if source else "全国"),
        }
    )
    anchor = {
        "reference_price": str(source.reference_price)
        if source and source.reference_price is not None
        else None,
        "reference_count": source.reference_count if source else 0,
        "notices": source.notices if source else [],
        "records": source.records if source else {},
    }
    history = body.traffic.history_experiment_id if body.traffic else None
    if history:
        from app.reporting import settled_traffic

        prior = get(db, workspace, history, "experiments")
        if not candidate_id or prior.data["candidate_id"] != candidate_id or prior.data["status"] != "CLOSED":
            raise BusinessError("TRAFFIC_SOURCE_CONFLICT", "历史流量必须来自同一商品已结束的测款")
        valid = next(
            (row for row in settled_traffic(db, workspace, prior) if row["channel"] == body.channel), None
        )
        if (
            valid is None
            or body.traffic.denominator != "CLICKS"
            or body.traffic.daily_visits != valid["daily_clicks"]
            or body.traffic.base != Decimal(valid["conversion"])
        ):
            raise BusinessError("TRAFFIC_SOURCE_CHANGED", "历史流量已变化，请重新带入，或改为手工假设", 409)
        anchor["traffic"] = valid
    return body, candidate_id, anchor


def evaluate(body, anchor):
    return forecast(body, decimal(anchor["reference_price"]), anchor["reference_count"], anchor["notices"])


def save_forecast(db, workspace, request):
    body, candidate_id, anchor = resolve_forecast(db, workspace, request.inputs)
    version = 1
    if request.parent_forecast_id:
        parent = db.scalar(
            query(workspace, "forecasts").where(Entity.id == request.parent_forecast_id).with_for_update()
        )
        if parent is None:
            raise BusinessError("NOT_FOUND", "预测方案不存在或不属于当前空间", 404)
        if latest(db, workspace, "forecasts", parent_forecast_id=parent.id):
            raise BusinessError("VERSION_CONFLICT", "该方案已有后续版本，请打开最新版本继续", 409)
        if (
            parent.data.get("candidate_id") != candidate_id
            or parent.data["inputs"]["report_id"] != body.report_id
        ):
            raise BusinessError("FORECAST_SOURCE_CONFLICT", "新版本必须使用同一个研究对象")
        version = parent.data["plan_version"] + 1
    inputs = exact_values(body)
    result = evaluate(body, anchor).model_dump(mode="json")
    return persist(
        db,
        workspace,
        "forecasts",
        {
            "name": request.name,
            "candidate_id": candidate_id,
            "market_report_id": body.report_id,
            "parent_forecast_id": request.parent_forecast_id,
            "history_experiment_id": body.traffic.history_experiment_id if body.traffic else None,
            "plan_version": version,
            "algorithm": ALGORITHM,
            "inputs": inputs,
            "anchor": anchor,
            "input_hash": fingerprint([ALGORITHM, inputs, anchor]),
            "result": result,
        },
        action="forecast_saved",
    )


def replay_forecast(saved):
    data = saved.data
    if data["algorithm"] != ALGORITHM:
        raise BusinessError("UNKNOWN_FORECAST_VERSION", "此计算版本只能查阅已保存结果", 409)
    return evaluate(ForecastInput.model_validate(data["inputs"]), data["anchor"])


def validate_attachment(db, workspace, candidate, experiment):
    saved = get(db, workspace, experiment["forecast_id"], "forecasts")
    data = saved.data
    body = ForecastInput.model_validate(data["inputs"])
    if data["candidate_id"] != candidate.id or body.procurement_mode != candidate_mode(candidate):
        raise BusinessError("FORECAST_SOURCE_CONFLICT", "预测方案与候选商品或采购方式不一致", 409)
    if not body.channel or not body.traffic or not data["result"]["scenarios"] or data["result"]["missing"]:
        raise BusinessError("FORECAST_INCOMPLETE", "请选用已填写测款渠道、费用和三档流量的预测方案", 409)
    if decimal(data["result"]["unit_contribution"]) <= 0:
        raise BusinessError("FORECAST_NOT_VIABLE", "当前方案的每单期望贡献非正，请重新核验定价和成本", 409)
    dates = beijing_dates(experiment)
    if dates is None or (dates[1] - dates[0]).days != body.traffic.days:
        raise BusinessError("FORECAST_WINDOW", "测款须按北京时间整日开始和结束，天数与预测期一致", 409)
    _, _, current = resolve_forecast(db, workspace, body)
    if current != data["anchor"]:
        raise BusinessError("FORECAST_STALE", "预测依据已变更，请在复核后另存方案并关联新版本", 409)
    normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
    if not normal or any(
        getattr(body, key) is None or money(getattr(body, key)) != money(normal.data[key])
        for key in ("c", "f")
    ):
        raise BusinessError("FORECAST_COST_CONFLICT", "预测的采购与运费须和已核验的正常利润情景一致", 409)
