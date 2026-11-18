"""Evidence-ranked candidates and reproducible pricing/volume scenarios."""

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal as D
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.db import now
from app.domain import iso, money
from app.errors import BusinessError
from app.market_connections import read_connection
from app.schemas import Contract, Money, Ratio
from app.selection import score_stale
from app.store import get, latest, require_evidence, rows
from app.workflows import comparisons, gate

PRICES = ("p", "c", "f", "b", "t", "a", "r", "l")


class Opportunity(Contract):
    id: str
    name: str
    category: str
    state: str
    keyword: str | None
    level: Literal["PRIORITY", "TEST", "VERIFY", "BLOCKED"]
    grade: str | None
    score: float | None
    score_low: float | None
    score_high: float | None
    coverage: float | None
    score_stale: bool
    demand_factor: float | None
    growth_factor: float | None
    missing: list[str]
    blocked: list[str]


class OpportunityPage(Contract):
    items: list[Opportunity]
    total: int
    page: int
    page_size: int


class ForecastCosts(Contract):
    c: Money | None = None
    f: Money | None = None
    b: Money | None = None
    t: Money | None = None
    a: Money | None = None
    r: Ratio | None = None
    l: Money | None = None  # noqa: E741 — shared P0 profit input key


class ForecastBaseline(Contract):
    name: str
    inputs: ForecastCosts
    price: Money | None
    reference_price: Money | None
    reference_count: int
    source: str
    source_id: str | None
    notices: list[str]


class Traffic(Contract):
    days: Annotated[int, Field(ge=1, le=365)]
    daily_visits: Annotated[int, Field(ge=0, le=10000000)]
    low: Ratio
    base: Ratio
    high: Ratio

    @model_validator(mode="after")
    def ordered(self):
        if not self.low <= self.base <= self.high:
            raise ValueError("转化率须按保守、基准、乐观从低到高填写")
        return self


class ForecastInput(ForecastCosts):
    price: Money | None = None
    target_margin: Ratio
    fixed_cost: Money | None = None
    traffic: Traffic | None = None
    candidate_id: str | None = None
    report_id: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if self.candidate_id and self.report_id:
            raise ValueError("同一次测算只选择一个候选或比价报告")
        return self


class ForecastScenario(Contract):
    name: Literal["LOW", "BASE", "HIGH"]
    conversion: Ratio
    orders: int
    paid_revenue: str
    retained_revenue: str | None
    variable_cost: str | None
    contribution: str | None
    after_fixed_cost: str | None


class ForecastResult(Contract):
    effective_price: str | None
    break_even_price: str | None
    minimum_target_price: str | None
    suggested_price: str | None
    max_purchase_cost: str | None
    max_cac: str | None
    unit_variable_cost: str | None
    unit_contribution: str | None
    unit_contribution_rate: float | None
    break_even_orders: int | None
    reference_price: str | None
    reference_count: int
    missing: list[str]
    notices: list[str]
    pricing_status: Literal["INCOMPLETE", "UNATTAINABLE", "COST_ONLY", "ABOVE_REFERENCE", "WITHIN_REFERENCE"]
    scenarios: list[ForecastScenario]


def opportunities(db, workspace, page, page_size, search):
    items = []
    if workspace == "public":
        return OpportunityPage(items=[], total=0, page=page, page_size=page_size)
    needle = search.casefold().strip()
    for candidate in rows(db, workspace, "candidates"):
        state = candidate.data["state"]
        if state in {"ARCHIVED", "REJECTED"}:
            continue
        sku = get(db, workspace, candidate.data["sku_id"], "skus")
        product = get(db, workspace, sku.data["product_id"], "products")
        keyword_id = candidate.data.get("keyword_id")
        keyword = get(db, workspace, keyword_id, "keywords").data["keyword"] if keyword_id else None
        if needle and not any(
            needle in term.casefold() for term in [sku.data["name"], product.data["category"], keyword or ""]
        ):
            continue
        requirements = gate(db, workspace, candidate)
        saved = latest(db, workspace, "scores", candidate_id=candidate.id)
        stale = score_stale(db, workspace, saved, candidate, requirements)
        score = saved.data if saved and not stale else {}
        factors = score.get("factors", {})
        grade = score.get("grade")
        blocked = list(requirements["blocked"])
        missing = list(requirements["missing"])
        if state == "HOLD":
            blocked.append("候选处于暂停状态")
        if not saved or stale:
            missing.append("评分已过期，需重新评分" if stale else "尚未完成机会评分")
        if factors.get("demand") is None:
            missing.append("需求证据不足")
        if blocked:
            level = "BLOCKED"
        elif requirements["gate"] == "PASS" and grade in {"S", "A"} and (factors.get("demand") or 0) > 0:
            level = "PRIORITY"
        elif requirements["gate"] == "PASS" and grade == "B" and (factors.get("demand") or 0) > 0:
            level = "TEST"
        else:
            level = "VERIFY"
        items.append(
            Opportunity(
                id=candidate.id,
                name=sku.data["name"],
                category=product.data["category"],
                state=state,
                keyword=keyword,
                level=level,
                grade=grade,
                score=score.get("score"),
                score_low=score.get("score_low") if score.get("display_interval") else None,
                score_high=score.get("score_high") if score.get("display_interval") else None,
                coverage=score.get("coverage"),
                score_stale=stale,
                demand_factor=factors.get("demand"),
                growth_factor=factors.get("growth"),
                blocked=blocked,
                missing=missing,
            )
        )
    order = {"PRIORITY": 0, "TEST": 1, "VERIFY": 2, "BLOCKED": 3}
    items.sort(
        key=lambda item: (
            order[item.level],
            -(item.score if item.score is not None else item.score_low or 0),
            -(item.coverage or 0),
            item.name,
            item.id,
        )
    )
    offset = (page - 1) * page_size
    return OpportunityPage(
        items=items[offset : offset + page_size], total=len(items), page=page, page_size=page_size
    )


def baseline(db, workspace, candidate_id=None, report_id=None):
    if workspace == "public":
        raise BusinessError("SPACE_MODE_CONFLICT", "公开历史空间的历史交易不用于当前定价，请切换空间", 409)
    if report_id:
        report = get(db, workspace, report_id, "market_reports")
        data, notices = report.data, []
        if any(iso(data[key]["observed_at"]) < now() - timedelta(hours=24) for key in ("supply", "retail")):
            notices.append("比价报告的报价已过期，请重新核验采购成本和竞品价")
        if data["result"]["match_state"] != "CONFIRMED":
            notices.append("比价报告中的规格尚未确认")
        if data["mode"] == "LIVE":
            connection = read_connection()
            if not connection.get("enabled") or connection.get("version") != data["provider_version"]:
                notices.append("平台连接已更新或停用")
        valid = not notices
        reference = data["result"]["retail_total"] if valid else None
        inputs = data["result"]["normal_inputs"] if valid else {}
        rounded = {key: money(inputs[key]) for key in PRICES if key != "r" and inputs.get(key) is not None}
        return ForecastBaseline(
            name=data["query"],
            inputs=ForecastCosts(**{k: rounded.get(k) for k in PRICES[1:] if k != "r"}, r=inputs.get("r")),
            price=rounded.get("p"),
            reference_price=reference,
            reference_count=int(reference is not None),
            source="MARKET_REPORT",
            source_id=report.id,
            notices=notices,
        )
    candidate = get(db, workspace, candidate_id, "candidates")
    sku = get(db, workspace, candidate.data["sku_id"], "skus")
    stats = comparisons(db, workspace, sku.id)["statistics"]
    normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
    inputs, notices = {}, []
    if normal:
        try:
            require_evidence(
                db, workspace, normal.data["evidence_id"], "profits", normal.data.get("evidence_version")
            )
            offer_id = candidate.data.get("primary_offer_id")
            if offer_id:
                offer = get(db, workspace, offer_id, "offers")
                require_evidence(
                    db, workspace, offer.data["evidence_id"], "offers", offer.data.get("evidence_version")
                )
                if (
                    normal.data.get("offer_version") != {"id": offer.id, "version": offer.version}
                    or iso(offer.data["expires_at"]) <= now()
                ):
                    raise BusinessError("STALE_COST", "主供报价已更新或过期")
            inputs = normal.data
        except BusinessError as exc:
            notices.append(exc.message + "，请重新核验成本")
    else:
        notices.append("此候选暂无有证据的正常利润情景，可手工录入成本")
    return ForecastBaseline(
        name=sku.data["name"],
        inputs=ForecastCosts(**{k: inputs.get(k) for k in PRICES[1:]}),
        price=inputs.get("p"),
        reference_price=stats.get("median"),
        reference_count=stats["count"],
        source="CANDIDATE",
        source_id=candidate.id,
        notices=notices,
    )


def ceiling(value):
    return value.quantize(D(".01"), rounding=ROUND_CEILING)


def forecast(body, reference_price=None, reference_count=0, notices=()):
    values = body.model_dump()
    missing = [k for k in PRICES[1:] if values[k] is None]
    zero = D(0)
    q = 1 - body.r if body.r is not None else None
    purchase_ceiling = cac = unit_cost = unit_contribution = margin = break_even_orders = None
    break_even = minimum = suggested = None
    status = "INCOMPLETE"
    if not missing:
        costs = body.c + body.f + body.b + body.t
        unit_cost = q * costs + body.r * body.l + body.a
        if q > 0:
            break_even = ceiling(unit_cost / q)
        if q <= body.target_margin:
            status = "UNATTAINABLE"
        else:
            minimum = ceiling(unit_cost / (q - body.target_margin))
            if reference_price is None:
                status = "COST_ONLY"
            elif reference_price < minimum:
                status = "ABOVE_REFERENCE"
            else:
                status = "WITHIN_REFERENCE"
                suggested = reference_price.quantize(D(".01"), rounding=ROUND_FLOOR)
            if status == "COST_ONLY":
                suggested = minimum

    price = body.price if body.price is not None else suggested
    if price is not None and unit_cost is not None:
        unit_contribution = q * price - unit_cost
        margin = float(unit_contribution / price) if price != 0 else None
        cac = unit_contribution + body.a
        if q > 0:
            purchase_ceiling = (
                ((q - body.target_margin) * price - body.r * body.l - body.a) / q - body.f - body.b - body.t
            )
            if purchase_ceiling >= 0:
                purchase_ceiling = purchase_ceiling.quantize(D(".01"), rounding=ROUND_FLOOR)
            else:
                purchase_ceiling = None
        if body.fixed_cost is not None and unit_contribution > 0:
            break_even_orders = int(
                (body.fixed_cost / unit_contribution).to_integral_value(rounding=ROUND_CEILING)
            )

    scenarios = []
    if body.traffic and price is not None:
        for label in ("LOW", "BASE", "HIGH"):
            conversion = getattr(body.traffic, label.lower())
            orders = int(body.traffic.days * body.traffic.daily_visits * conversion)
            revenue = orders * price
            contribution = orders * unit_contribution if unit_contribution is not None else None
            scenarios.append(
                ForecastScenario(
                    name=label,
                    conversion=conversion,
                    orders=orders,
                    paid_revenue=money(revenue),
                    retained_revenue=money(revenue * q) if q is not None else None,
                    variable_cost=money(orders * unit_cost) if unit_cost is not None else None,
                    contribution=money(contribution) if contribution is not None else None,
                    after_fixed_cost=money(contribution - body.fixed_cost)
                    if contribution is not None and body.fixed_cost is not None
                    else None,
                )
            )
    return ForecastResult(
        effective_price=money(price) if price is not None else None,
        break_even_price=money(break_even) if break_even is not None else None,
        minimum_target_price=money(minimum) if minimum is not None else None,
        suggested_price=money(suggested) if suggested is not None else None,
        max_purchase_cost=money(purchase_ceiling) if purchase_ceiling is not None else None,
        max_cac=money(max(cac, zero)) if cac is not None else None,
        unit_variable_cost=money(unit_cost) if unit_cost is not None else None,
        unit_contribution=money(unit_contribution) if unit_contribution is not None else None,
        unit_contribution_rate=margin,
        break_even_orders=break_even_orders,
        reference_price=money(reference_price) if reference_price is not None else None,
        reference_count=reference_count,
        missing=missing,
        notices=list(notices),
        pricing_status=status,
        scenarios=scenarios,
    )
