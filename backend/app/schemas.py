from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
    field_validator,
)


def parse_money(value):
    if not isinstance(value, (str, Decimal)):
        raise ValueError("金额必须使用十进制字符串")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(parse_money),
    Field(ge=0, max_digits=18, decimal_places=4),
    WithJsonSchema({"type": "string", "pattern": r"^\d+(\.\d{1,4})?$"}),
]
SignedMoney = Annotated[
    Decimal,
    BeforeValidator(parse_money),
    Field(max_digits=18, decimal_places=4),
    WithJsonSchema({"type": "string", "pattern": r"^-?\d+(\.\d{1,4})?$"}),
]
Ratio = Annotated[Decimal, Field(ge=0, le=1), PlainSerializer(float, return_type=float)]
Rating = Annotated[Decimal, Field(ge=0, le=100), PlainSerializer(float, return_type=float)]
Text = Annotated[str, Field(min_length=1, max_length=500)]
ProcurementMode = Literal["STOCK", "DROPSHIP"]
PositiveQuantity = Annotated[int, Field(gt=0, le=1000000)]
State = Literal[
    "DISCOVERED",
    "RESEARCHING",
    "READY_SAMPLE",
    "SAMPLE_PASSED",
    "READY_TEST",
    "TESTING",
    "SCALE",
    "HOLD",
    "REJECTED",
    "ARCHIVED",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_default=True)

    @model_validator(mode="after")
    def aware_times(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime) and value.tzinfo is None:
                raise ValueError(f"{name} 必须包含时区")
            if isinstance(value, datetime):
                setattr(self, name, value.astimezone(UTC))
        return self


class Source(Contract):
    name: Text
    platform: Text
    permission_scope: list[str] = Field(min_length=1)
    permission_note: Text
    enabled: bool = True
    expires_at: datetime | None = None
    license: str = ""
    origin_url: str = ""


class Evidence(Contract):
    source_id: str
    locator: Text
    observed_at: datetime
    expires_at: datetime | None = None
    quality_flag: Literal["valid", "conflict", "rejected"] = "valid"
    precision: Literal["exact", "lower_bound", "range", "estimate", "unknown"] = "exact"
    note: str = Field(default="", max_length=3000)
    market_report_id: str | None = None


class Keyword(Contract):
    source_id: str
    evidence_id: str
    keyword: Text
    category: Text
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    metric_definition: Text = "search_index"
    search_index: Annotated[Decimal, Field(ge=0)] | None = None
    listing_count: Annotated[int, Field(ge=0)] | None = None
    conversion: Ratio | None = None
    mall_click_share: Ratio | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.window_end <= self.window_start:
            raise ValueError("INVALID_WINDOW: 结束时间必须晚于开始时间")
        return self


class Product(Contract):
    name: Text
    category: Text
    brand: str = ""


class Sku(Contract):
    product_id: str
    name: Text
    spec_key: Text
    quantity: Annotated[int, Field(gt=0)]
    attributes: dict[str, str] = Field(default_factory=dict)


class Listing(Contract):
    platform: Text
    external_id: Text
    external_sku_id: Text
    title: Text
    url: str = Field(default="", max_length=2000, pattern=r"^(https?://[^\s]+)?$")
    quantity: Annotated[int, Field(gt=0)]
    attributes: dict[str, str] = Field(default_factory=dict)


class Match(Contract):
    listing_id: str
    sku_id: str
    evidence_id: str
    reason: Text


class MatchReview(Contract):
    state: Literal["MATCHED", "REJECTED", "ALTERNATIVE"]
    evidence_id: str
    reason: Text
    expected_version: Annotated[int, Field(gt=0)]


class Price(Contract):
    listing_id: str
    evidence_id: str
    observed_at: datetime
    price_kind: Literal["LIST", "ELIGIBLE", "DROPSHIP", "WHOLESALE"] = "LIST"
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    region: Text
    quantity: Annotated[int, Field(gt=0)]
    price: Money
    shipping: Money | None = None
    conditions: str = ""
    eligibility_verified: bool = False


class Supplier(Contract):
    name: Text
    platform: Text = "1688"
    external_id: Text
    note: str = ""


class Signal(Contract):
    listing_id: str
    evidence_id: str
    metric_kind: Literal["SOLD", "GROUP_BUY", "REVIEW_COUNT", "ESTIMATED_SALES"]
    unit: Literal["ORDERS", "ITEMS", "REVIEWS"]
    raw_value: Text
    value: Annotated[Decimal, Field(ge=0)] | None = None
    upper_value: Annotated[Decimal, Field(ge=0)] | None = None
    precision: Literal["exact", "lower_bound", "range", "estimate", "unknown"]
    observed_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="after")
    def valid_metric(self):
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("统计窗口必须同时提供开始和结束时间")
        if self.window_start and self.window_end <= self.window_start:
            raise ValueError("INVALID_WINDOW")
        if self.window_end and self.window_end > self.observed_at:
            raise ValueError("统计窗口尚未结束")
        if self.upper_value is not None and (self.value is None or self.upper_value < self.value):
            raise ValueError("数值区间倒置")
        if self.metric_kind == "REVIEW_COUNT" and self.unit != "REVIEWS":
            raise ValueError("评价数的单位必须为 REVIEWS")
        if self.precision == "exact" and (self.value is None or self.metric_kind == "ESTIMATED_SALES"):
            raise ValueError("估算或缺失数值不能标为精确销量")
        return self


class Offer(Contract):
    supplier_id: str
    sku_id: str
    evidence_id: str
    dropship: bool | None = None
    procurement_mode: ProcurementMode | None = None
    quantity: Annotated[int, Field(gt=0)]
    unit_cost: Money
    shipping: Money | None = None
    service_fee: Money | None = None
    supplier_pack: PositiveQuantity | None = None
    min_order: PositiveQuantity | None = None
    batch_quantity: PositiveQuantity | None = None
    outbound_shipping: Money | None = None
    currency: Literal["CNY"] = "CNY"
    region: Text
    lead_hours: Annotated[int, Field(ge=0)]
    expires_at: datetime
    return_terms: Text
    verified: bool = False

    @model_validator(mode="after")
    def procurement(self):
        legacy_dropship = self.procurement_mode is None and self.dropship is True
        if self.procurement_mode is None:
            if self.dropship is None:
                raise ValueError("请选择采购方式")
            self.procurement_mode = "DROPSHIP" if self.dropship else "STOCK"
        if self.dropship is not None and self.dropship != (self.procurement_mode == "DROPSHIP"):
            raise ValueError("报价的采购方式与代发条件不一致")
        self.dropship = self.procurement_mode == "DROPSHIP"
        if self.dropship and self.verified:
            if legacy_dropship and self.min_order is None:
                self.min_order = 1
            if self.min_order != 1:
                raise ValueError("核验代发报价时需确认一件起订")
        return self


class Sample(Contract):
    offer_id: str
    evidence_id: str
    result: Literal["PENDING", "PASSED", "FAILED"]
    reason: Text


class Candidate(Contract):
    sku_id: str
    procurement_mode: ProcurementMode = "DROPSHIP"
    market_report_id: str | None = None
    keyword_id: str | None = None
    primary_offer_id: str | None = None
    backup_offer_id: str | None = None
    note: str = ""


class ProfitInput(Contract):
    p: Money
    c: Money
    f: Money
    b: Money
    t: Money
    fee_rate: Ratio = 0
    a: Money
    r: Ratio
    l: Money


class Profit(ProfitInput):
    candidate_id: str
    evidence_id: str
    label: Literal["NORMAL", "STRESS"]
    note: str = ""


class Cashflow(Contract):
    daily_orders: Annotated[Decimal, Field(ge=0)]
    upfront_cost: Money
    settlement_days: Annotated[int, Field(ge=0)]
    refund_reserve: Money
    advertising_budget: Money


class Assessment(Contract):
    candidate_id: str
    evidence_id: str
    reason: Text
    fulfillment_score: Rating | None = None
    quality_score: Rating | None = None
    backup_score: Rating | None = None
    usage_risk_score: Rating | None = None
    differentiation_score: Rating | None = None
    head_share: Ratio | None = None
    head_share_sample_count: Annotated[int, Field(ge=0)] = 0
    head_share_precision: Literal["exact", "lower_bound", "estimate", "unknown"] = "unknown"
    distinct_reviews: Annotated[int, Field(ge=0)] | None = None
    pain_verified: bool | None = None
    pain_solvable: bool | None = None
    bundle_fulfillment_verified: bool | None = None
    single_contribution: Money | None = None
    bundle_contribution: Money | None = None


class Risk(Contract):
    candidate_id: str
    evidence_id: str
    risk_code: Literal["IP", "QUALIFICATION", "SAFETY", "FULFILLMENT"]
    status: Literal["PENDING", "PASS", "BLOCKED"]
    reason: Text


class Strategy(Contract):
    name: Text
    version: Text
    config: dict


class Experiment(Contract):
    candidate_id: str
    forecast_id: str | None = None
    start_at: datetime
    end_at: datetime
    budget: Money
    loss_limit: Money
    target_clicks: Annotated[int, Field(gt=0)] = 100
    hypothesis: Text

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_at <= self.start_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class Funnel(Contract):
    experiment_id: str
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    channel: Text
    impressions: Annotated[int, Field(ge=0)] | None = None
    clicks: Annotated[int, Field(ge=0)] | None = None
    paid_orders: Annotated[int, Field(ge=0)] | None = None
    ad_spend: Money | None = None
    evidence_id: str

    @field_validator("business_date")
    @classmethod
    def valid_business_date(cls, value):
        date.fromisoformat(value)
        return value


class Order(Contract):
    source_id: str
    evidence_id: str
    experiment_id: str
    external_order_id: Text
    line_id: Text
    paid_at: datetime
    channel: Text
    quantity: Annotated[int, Field(gt=0)] = 1
    revenue: Money
    customer_refund: Money = "0"
    purchase: Money | None = None
    purchase_refund: Money | None = None
    shipping: Money | None = None
    packaging: Money | None = None
    net_fees: SignedMoney | None = None
    advertising: Money | None = None
    other_net_cost: SignedMoney | None = None
    mature: bool = False
    settled: bool = False
    note: str = ""


class Transaction(Contract):
    source_id: str
    evidence_id: str
    invoice_no: Text
    stock_code: Text
    description: str = ""
    quantity: int
    invoice_at: datetime
    unit_price: Money
    currency: Literal["GBP"] = "GBP"
    country: str = ""
    source_row: Annotated[int, Field(gt=0)]


class Transition(Contract):
    target_state: State
    expected_version: Annotated[int, Field(gt=0)]
    reason: Text
    evidence_ids: list[str] = Field(default_factory=list)


class Login(Contract):
    password: str = Field(min_length=1, max_length=300)


class EntityOut(BaseModel):
    id: str
    workspace: str
    kind: str
    version: int
    data: dict
    created_at: datetime
    updated_at: datetime


class EntityPage(BaseModel):
    items: list[EntityOut]
    total: int
    page: int
    page_size: int


CONTRACTS = {
    "sources": Source,
    "evidence": Evidence,
    "keywords": Keyword,
    "products": Product,
    "skus": Sku,
    "listings": Listing,
    "matches": Match,
    "prices": Price,
    "suppliers": Supplier,
    "offers": Offer,
    "samples": Sample,
    "candidates": Candidate,
    "profits": Profit,
    "assessments": Assessment,
    "risks": Risk,
    "strategies": Strategy,
    "experiments": Experiment,
    "funnels": Funnel,
    "orders": Order,
    "transactions": Transaction,
    "signals": Signal,
}

REFERENCES = {
    "market_run_id": "market_runs",
    "market_report_id": "market_reports",
    "forecast_id": "forecasts",
    "parent_forecast_id": "forecasts",
    "history_experiment_id": "experiments",
    "source_id": "sources",
    "evidence_id": "evidence",
    "product_id": "products",
    "sku_id": "skus",
    "listing_id": "listings",
    "supplier_id": "suppliers",
    "offer_id": "offers",
    "candidate_id": "candidates",
    "keyword_id": "keywords",
    "experiment_id": "experiments",
    "primary_offer_id": "offers",
    "backup_offer_id": "offers",
}
