"""Evidence-ranked candidates and reproducible pricing/volume scenarios."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal as D
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.db import now
from app.domain import decimal, expected_cost, iso, money, profit, stock_batch
from app.errors import BusinessError
from app.market_service import report_draft, report_expires_at, require_report_current
from app.opportunity_data import opportunity_snapshot
from app.schemas import Contract, Money, ProcurementMode, Ratio, Text
from app.selection import keyword_current, score_stale
from app.store import get, latest, require_evidence, rows
from app.workflows import candidate_mode, comparisons, config_for, gate

PRICES = ("p", "c", "f", "b", "t", "a", "r", "l")
ALGORITHM = "forecast-2"


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
    procurement_mode: ProcurementMode
    next_action: Literal["RESEARCH", "BUY_SAMPLE", "START_TEST", "TRACK_TEST", "HOLD"]


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
    fee_rate: Ratio = 0
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
    procurement_mode: ProcurementMode
    region: str
    stock_units: int | None = None
    stock_batch_cash: Money | None = None
    records: dict[str, int]
    expires_at: str | None = None


class Conversions(Contract):
    low: Ratio
    base: Ratio
    high: Ratio

    @model_validator(mode="after")
    def ordered(self):
        if not self.low <= self.base <= self.high:
            raise ValueError("转化率须按保守、基准、乐观从低到高填写")
        return self


class Traffic(Conversions):
    days: Annotated[int, Field(ge=1, le=365)]
    daily_visits: Annotated[int, Field(ge=0, le=10000000)]
    denominator: Literal["VISITS", "CLICKS"] = "VISITS"
    history_experiment_id: str | None = None


class AlternativePrice(Conversions):
    price: Money


class ForecastInput(ForecastCosts):
    price: Money | None = None
    target_margin: Ratio
    fixed_cost: Money | None = None
    traffic: Traffic | None = None
    candidate_id: str | None = None
    report_id: str | None = None
    procurement_mode: ProcurementMode | None = None
    region: Text | None = None
    refund_amount_rate: Ratio | None = None
    channel: Text | None = None
    stock_units: Annotated[int, Field(ge=0, le=1000000000000)] | None = None
    stock_batch_cash: Money | None = None
    dropship_capacity_per_day: Annotated[int, Field(ge=0, le=10000000)] | None = None
    settlement_days: Annotated[int, Field(ge=0, le=365)] | None = None
    advertising_budget: Money | None = None
    refund_reserve: Money | None = None
    alternative_prices: list[AlternativePrice] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def one_source(self):
        if self.candidate_id and self.report_id:
            raise ValueError("同一次测算只选择一个候选或比价报告")
        if self.alternative_prices and not self.traffic:
            raise ValueError("比较不同售价前需填写预测期和访问量")
        prices = [choice.price for choice in self.alternative_prices]
        if len(set(prices)) != len(prices) or self.price in prices:
            raise ValueError("每个售价方案需使用不同价格")
        return self


class ForecastScenario(Contract):
    name: Literal["LOW", "BASE", "HIGH"]
    conversion: Ratio
    orders: int
    demand_orders: int
    shortfall: int
    remaining_stock: int | None
    paid_revenue: str
    retained_revenue: str | None
    net_revenue: str | None
    advertising_cost: str | None
    cash_required: str | None
    variable_cost: str | None
    contribution: str | None
    after_fixed_cost: str | None


class PriceForecast(Contract):
    price: str
    scenarios: list[ForecastScenario]


class ForecastResult(Contract):
    effective_price: str | None
    break_even_price: str | None
    minimum_target_price: str | None
    suggested_price: str | None
    max_purchase_cost: str | None = None
    max_cac: str | None = None
    max_target_cac: str | None = None
    reference_price_gap: str | None
    reference_cost_reduction: str | None
    unit_variable_cost: str | None = None
    unit_contribution: str | None = None
    unit_contribution_rate: float | None = None
    break_even_orders: int | None = None
    reference_price: str | None
    reference_count: int
    missing: list[str]
    notices: list[str]
    pricing_status: Literal["INCOMPLETE", "UNATTAINABLE", "COST_ONLY", "ABOVE_REFERENCE", "WITHIN_REFERENCE"]
    scenarios: list[ForecastScenario]
    alternative_scenarios: list[PriceForecast]


def opportunities(db, workspace, page, page_size, search):
    items = []
    if workspace == "public":
        return OpportunityPage(items=[], total=0, page=page, page_size=page_size)
    candidates, db, cohort_dates = opportunity_snapshot(db, workspace, search.casefold().strip())
    strategy = config_for(db, workspace) if candidates else None
    for candidate in candidates:
        state = candidate.data["state"]
        sku = get(db, workspace, candidate.data["sku_id"], "skus")
        product = get(db, workspace, sku.data["product_id"], "products")
        keyword_id = candidate.data.get("keyword_id")
        keyword = get(db, workspace, keyword_id, "keywords") if keyword_id else None
        requirements = gate(db, workspace, candidate)
        saved = latest(db, workspace, "scores", candidate_id=candidate.id)
        stale = score_stale(db, workspace, saved, candidate, requirements, cohort_dates)
        score = saved.data if saved and not stale else {}
        factors = score.get("factors", {})
        grade = score.get("grade")
        blocked = list(requirements["blocked"])
        missing = list(requirements["missing"])
        if state == "HOLD":
            blocked.append("候选处于暂停状态")
        if not saved or stale:
            missing.append("评分已过期，需重新评分" if stale else "尚未完成机会评分")
        has_demand = (
            keyword is not None
            and keyword_current(db, workspace, keyword, strategy)
            and any(decimal(keyword.data.get(key) or 0) > 0 for key in ("search_index", "conversion"))
        )
        if not has_demand:
            missing.append("需求证据不足")
        economics = False
        normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
        if not blocked and has_demand and normal:
            try:
                require_evidence(
                    db, workspace, normal.data["evidence_id"], "profits", normal.data.get("evidence_version")
                )
                offer_id = candidate.data.get("primary_offer_id")
                offer = get(db, workspace, offer_id, "offers") if offer_id else None
                economics = (
                    decimal(profit(normal.data)["contribution"]) > 0
                    and bool(
                        offer
                        and normal.data.get("offer_version") == {"id": offer.id, "version": offer.version}
                    )
                    and comparisons(db, workspace, sku.id, offer.data["region"])["statistics"]["count"] > 0
                    and (
                        requirements["gate"] == "PASS"
                        or gate(db, workspace, candidate, "READY_SAMPLE")["gate"] == "PASS"
                    )
                )
            except BusinessError:
                pass
        if has_demand and not economics and not blocked:
            missing.append("待核验可用主供、正向利润与同款到手价")
        if blocked:
            level = "BLOCKED"
        elif economics and (grade != "D" and grade != "C"):
            level = "PRIORITY"
        elif economics:
            level = "TEST"
        else:
            level = "VERIFY"
        action = "RESEARCH"
        if blocked:
            action = "HOLD"
        elif state in {"TESTING", "SCALE"}:
            action = "TRACK_TEST" if requirements["gate"] == "PASS" else "HOLD"
        elif requirements["gate"] == "PASS" and state in {"SAMPLE_PASSED", "READY_TEST"}:
            action = "START_TEST"
        elif economics and state in {"RESEARCHING", "READY_SAMPLE"}:
            action = "BUY_SAMPLE"
        items.append(
            Opportunity(
                id=candidate.id,
                name=sku.data["name"],
                category=product.data["category"],
                state=state,
                keyword=keyword.data["keyword"] if keyword else None,
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
                procurement_mode=candidate_mode(candidate),
                next_action=action,
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


def pricing_inputs(values, rounded=False):
    return ForecastCosts(
        **{
            key: (money(values[key]) if rounded else values[key])
            for key in PRICES[1:]
            if key != "r" and values.get(key) is not None
        },
        r=values.get("r"),
        fee_rate=values.get("fee_rate", 0),
    )


def baseline(db, workspace, candidate_id=None, report_id=None, region=None):
    if workspace == "public":
        raise BusinessError("SPACE_MODE_CONFLICT", "请在模拟演示或真实经营空间测算当前商品", 409)
    if report_id:
        report = get(db, workspace, report_id, "market_reports")
        data, notices = report.data, []
        try:
            require_report_current(db, workspace, report)
            if data.get("evidence_id"):
                require_evidence(db, workspace, data["evidence_id"])
        except BusinessError as exc:
            notices.append(exc.message)
        if data["result"]["match_state"] != "CONFIRMED":
            notices.append("比价报告中的规格尚未确认")
        if region and region != data["inputs"]["region"]:
            notices.append("报告报价地区与测算地区不一致")
        reference = data["result"]["retail_total"] if not notices else None
        inputs = data["result"]["normal_inputs"] if not notices else {}
        return ForecastBaseline(
            name=data["query"],
            inputs=pricing_inputs(inputs, rounded=True),
            price=money(inputs["p"]) if inputs.get("p") is not None else None,
            reference_price=reference,
            reference_count=int(reference is not None),
            source="MARKET_REPORT",
            source_id=report.id,
            notices=notices,
            procurement_mode=data["inputs"]["procurement_mode"],
            region=data["inputs"]["region"],
            stock_units=data["result"].get("sellable_units") if not notices else None,
            stock_batch_cash=data["result"].get("stock_batch_cost") if not notices else None,
            records={report.id: report.version},
            expires_at=report_expires_at(db, workspace, report).isoformat(),
        )

    candidate = get(db, workspace, candidate_id, "candidates")
    sku = get(db, workspace, candidate.data["sku_id"], "skus")
    normal = latest(db, workspace, "profits", candidate_id=candidate.id, label="NORMAL")
    offer_id = candidate.data.get("primary_offer_id")
    offer = get(db, workspace, offer_id, "offers") if offer_id else None
    mode = candidate_mode(candidate)
    selected_region = region or (offer.data["region"] if offer else None)
    linked_report = None
    if normal is None:
        linked_report = next(
            (
                report
                for report in rows(db, workspace, "market_reports", candidate_id=candidate.id)
                if report.data["inputs"]["procurement_mode"] == mode
                and (selected_region is None or report.data["inputs"]["region"] == selected_region)
            ),
            None,
        )
    selected_region = selected_region or (linked_report.data["inputs"]["region"] if linked_report else "全国")
    related = comparisons(db, workspace, sku.id, selected_region)
    stats = related["statistics"]
    versions = {sku.id: sku.version}
    for quote in related["included"]:
        versions.update(quote["versions"])
    inputs, notices = {}, []
    stock_units, stock_cash = None, None
    if normal:
        try:
            evidence = require_evidence(
                db, workspace, normal.data["evidence_id"], "profits", normal.data.get("evidence_version")
            )
            if normal.data.get("procurement_mode", "DROPSHIP") != mode:
                raise BusinessError("STALE_COST", "采购方式已变更")
            if offer:
                require_evidence(
                    db, workspace, offer.data["evidence_id"], "offers", offer.data.get("evidence_version")
                )
                if (
                    normal.data.get("offer_version") != {"id": offer.id, "version": offer.version}
                    or iso(offer.data["expires_at"]) <= now()
                    or offer.data["region"] != selected_region
                ):
                    raise BusinessError("STALE_COST", "主供报价已更新、过期或不适用当前地区")
                versions[offer.id] = offer.version
                if mode == "STOCK" and all(
                    offer.data.get(key) is not None for key in ("batch_quantity", "supplier_pack", "shipping")
                ):
                    stock_units, stock_cash, _ = stock_batch(
                        offer.data["batch_quantity"],
                        offer.data["supplier_pack"],
                        sku.data["quantity"],
                        decimal(offer.data["unit_cost"]),
                        decimal(offer.data["shipping"]),
                    )
            versions.update({normal.id: normal.version, evidence.id: evidence.version})
            inputs = normal.data
        except BusinessError as exc:
            notices.append(exc.message + "，请重新核验成本")
    elif linked_report:
        draft = report_draft(db, workspace, linked_report, candidate)
        if draft["stale_reason"]:
            notices.append(draft["stale_reason"])
        else:
            inputs = {key: value for key, value in draft["normal"].items() if key in (*PRICES, "fee_rate")}
            stock_units = linked_report.data["result"].get("sellable_units")
            stock_cash = decimal(linked_report.data["result"].get("stock_batch_cost"))
            versions[linked_report.id] = linked_report.version
            notices.append("比价成本为草稿，采用到测款前请先确认主供、费用和利润情景")
    else:
        notices.append("此候选暂无有证据的正常利润情景，可手工录入成本")
    return ForecastBaseline(
        name=sku.data["name"],
        inputs=pricing_inputs(inputs),
        price=inputs.get("p"),
        reference_price=stats.get("median"),
        reference_count=stats["count"],
        source="CANDIDATE",
        source_id=candidate.id,
        notices=notices,
        procurement_mode=mode,
        region=selected_region,
        stock_units=stock_units,
        stock_batch_cash=money(stock_cash) if stock_cash is not None else None,
        records=versions,
        expires_at=offer.data["expires_at"] if offer else None,
    )


def ceiling(value):
    return value.quantize(D(".01"), rounding=ROUND_CEILING)


def scenario_results(body, price, rates, fixed, slope):
    if body.traffic is None or price is None:
        return []
    horizon = body.traffic.days
    capacity = None
    if body.procurement_mode == "STOCK":
        capacity = body.stock_units
    elif body.procurement_mode == "DROPSHIP" and body.dropship_capacity_per_day is not None:
        capacity = body.dropship_capacity_per_day * horizon
    unit_cost = fixed + (1 - body.r) * body.fee_rate * price if fixed is not None else None
    unit_contribution = slope * price - fixed if fixed is not None else None
    results = []
    for label in ("LOW", "BASE", "HIGH"):
        conversion = getattr(rates, label.lower())
        demand = int(horizon * body.traffic.daily_visits * conversion)
        orders = min(demand, capacity) if capacity is not None else demand
        revenue = orders * price
        contribution = orders * unit_contribution if unit_contribution is not None else None
        cash = None
        if body.advertising_budget is not None and body.refund_reserve is not None:
            budget_and_reserve = body.advertising_budget + body.refund_reserve
            if body.procurement_mode == "STOCK" and body.stock_batch_cash is not None:
                cash = body.stock_batch_cash + budget_and_reserve
            if body.procurement_mode == "DROPSHIP" and all(
                value is not None for value in (body.c, body.f, body.b, body.settlement_days)
            ):
                open_orders = min(
                    orders,
                    int(
                        (D(orders) * body.settlement_days / horizon).to_integral_value(rounding=ROUND_CEILING)
                    ),
                )
                cash = open_orders * (body.c + body.f + body.b) + budget_and_reserve
        results.append(
            ForecastScenario(
                name=label,
                conversion=conversion,
                orders=orders,
                demand_orders=demand,
                shortfall=demand - orders,
                remaining_stock=capacity - orders
                if body.procurement_mode == "STOCK" and capacity is not None
                else None,
                paid_revenue=money(revenue),
                retained_revenue=money(revenue * (1 - body.r)) if body.r is not None else None,
                net_revenue=money(revenue * (1 - body.refund_amount_rate))
                if body.refund_amount_rate is not None
                else None,
                advertising_cost=money(orders * body.a) if body.a is not None else None,
                cash_required=money(cash) if cash is not None else None,
                variable_cost=money(orders * unit_cost) if unit_cost is not None else None,
                contribution=money(contribution) if contribution is not None else None,
                after_fixed_cost=money(contribution - body.fixed_cost)
                if contribution is not None and body.fixed_cost is not None
                else None,
            )
        )
    return results


def forecast(body, reference_price=None, reference_count=0, notices=()):
    values = {key: getattr(body, key) for key in (*PRICES[1:], "fee_rate")}
    missing = [key for key in PRICES[1:] if values[key] is None]
    fixed, slope = expected_cost(values) if not missing else (None, None)
    break_even = minimum = suggested = None
    status = "INCOMPLETE"
    if fixed is not None:
        if slope > 0:
            break_even = ceiling(fixed / slope)
        if slope <= body.target_margin:
            status = "UNATTAINABLE"
        else:
            minimum = ceiling(fixed / (slope - body.target_margin))
            if reference_price is None:
                status, suggested = "COST_ONLY", minimum
            elif reference_price < minimum:
                status = "ABOVE_REFERENCE"
            else:
                status, suggested = (
                    "WITHIN_REFERENCE",
                    reference_price.quantize(D(".01"), rounding=ROUND_FLOOR),
                )

    price = body.price if body.price is not None else suggested
    targets = {}
    if fixed is not None and price is not None:
        contribution = slope * price - fixed
        unit_cost = fixed + (1 - body.r) * body.fee_rate * price
        cac = contribution + body.a
        targets.update(
            unit_contribution=money(contribution),
            unit_variable_cost=money(unit_cost),
            unit_contribution_rate=float(contribution / price) if price else None,
            max_cac=money(max(cac, D(0))),
            max_target_cac=money(max(cac - body.target_margin * price, D(0))),
        )
        if body.r < 1:
            purchase_ceiling = body.c + (contribution - body.target_margin * price) / (1 - body.r)
            if purchase_ceiling >= 0:
                targets["max_purchase_cost"] = money(
                    purchase_ceiling.quantize(D(".01"), rounding=ROUND_FLOOR)
                )
        if body.fixed_cost is not None and contribution > 0:
            targets["break_even_orders"] = int(
                (body.fixed_cost / contribution).to_integral_value(rounding=ROUND_CEILING)
            )
    gap, reduction = None, None
    if minimum is not None and reference_price is not None and reference_price < minimum:
        gap = minimum - reference_price
        reduction = ceiling((fixed - reference_price * (slope - body.target_margin)) / (1 - body.r))
    if any(choice.price == price for choice in body.alternative_prices):
        raise BusinessError("DUPLICATE_PRICE", "备选售价与本次采用的主售价相同")
    alternatives = [
        PriceForecast(
            price=money(choice.price), scenarios=scenario_results(body, choice.price, choice, fixed, slope)
        )
        for choice in body.alternative_prices
    ]
    notices = list(notices)
    if body.traffic and body.procurement_mode == "STOCK" and body.stock_units is None:
        notices.append("可用库存未知，订单按未扣库存限制的需求试算")
    if body.traffic and body.procurement_mode == "DROPSHIP" and body.dropship_capacity_per_day is None:
        notices.append("代发日履约上限未知，订单按未扣产能限制的需求试算")
    return ForecastResult(
        effective_price=money(price) if price is not None else None,
        break_even_price=money(break_even) if break_even is not None else None,
        minimum_target_price=money(minimum) if minimum is not None else None,
        suggested_price=money(suggested) if suggested is not None else None,
        reference_price_gap=money(gap) if gap is not None else None,
        reference_cost_reduction=money(reduction) if reduction is not None else None,
        reference_price=money(reference_price) if reference_price is not None else None,
        reference_count=reference_count,
        missing=missing,
        notices=notices,
        pricing_status=status,
        scenarios=scenario_results(body, price, body.traffic, fixed, slope),
        alternative_scenarios=alternatives,
        **targets,
    )
