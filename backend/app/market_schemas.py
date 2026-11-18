"""Contracts for online product discovery and conservative purchase research."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas import Contract, Money, PositiveQuantity, ProcurementMode, Ratio, Text

Platform = Literal["1688", "taobao", "jd", "pinduoduo"]


class ConnectionInput(Contract):
    enabled: bool = True
    api_key: str | None = Field(default=None, max_length=500)
    api_secret: str | None = Field(default=None, max_length=500)
    access_token: str | None = Field(default=None, max_length=2000)
    taobao_route: Literal["tglobal", "taobao"] = "tglobal"
    platforms: list[Platform] = Field(
        default_factory=lambda: ["1688", "taobao", "jd", "pinduoduo"], min_length=1, max_length=4
    )
    permission_note: str = Field(default="", max_length=1000)


class SearchInput(Contract):
    query: str = Field(min_length=1, max_length=100)
    platforms: list[Platform] = Field(
        default_factory=lambda: ["1688", "taobao", "jd", "pinduoduo"], min_length=2, max_length=4
    )
    per_platform: Annotated[int, Field(ge=1, le=5)] = 3
    page: Annotated[int, Field(ge=1, le=20)] = 1
    mode: Literal["LIVE", "DEMO"] = "LIVE"

    @model_validator(mode="after")
    def unique_platforms(self):
        self.platforms = list(dict.fromkeys(self.platforms))
        if "1688" not in self.platforms or len(self.platforms) < 2:
            raise ValueError("请选择1688及至少一个零售平台")
        return self


class PriceTier(Contract):
    min_quantity: PositiveQuantity
    price: Money


class ManualQuote(Contract):
    platform: Platform
    title: Text
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    variant_key: str = Field(default="", max_length=200)
    seller: str = Field(default="", max_length=200)
    price: Money
    pack_count: Annotated[int, Field(gt=0, le=100000)] | None = None
    material: str = Field(default="", max_length=500)
    size: str = Field(default="", max_length=500)
    brand: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=500)
    min_order: Annotated[int, Field(gt=0, le=1000000)] | None = None
    price_tiers: list[PriceTier] = Field(default_factory=list, max_length=20)
    price_kind: Literal["LIST", "ELIGIBLE"] = "LIST"
    eligibility_verified: bool = False
    conditions: str = Field(default="", max_length=800)
    shipping: Money | None = None
    shipping_region: str = Field(default="", max_length=100)
    observed_at: datetime | None = None
    evidence_note: Annotated[str, Field(min_length=5, max_length=1500)]

    @model_validator(mode="after")
    def quote_terms(self):
        quantities = [tier.min_quantity for tier in self.price_tiers]
        if len(quantities) != len(set(quantities)):
            raise ValueError("同一起订量只能填写一个阶梯价")
        if self.platform != "1688" and self.price_tiers:
            raise ValueError("只为1688货源填写进货阶梯价")
        if self.price_kind == "ELIGIBLE" and self.eligibility_verified and not self.conditions:
            raise ValueError("确认优惠资格时需记录适用条件")
        return self


class ManualRunInput(Contract):
    query: Annotated[str, Field(min_length=1, max_length=100)]
    quotes: list[ManualQuote] = Field(min_length=2, max_length=30)

    @model_validator(mode="after")
    def platforms(self):
        platforms = {quote.platform for quote in self.quotes}
        if "1688" not in platforms or len(platforms) < 2:
            raise ValueError("请至少提供一条1688货源及一条零售报价")
        return self


class ManualRunRevision(ManualRunInput):
    expected_version: Annotated[int, Field(gt=0)]


class CompareInput(Contract):
    expected_version: Annotated[int, Field(gt=0)]
    supply_id: Text
    retail_id: Text
    supply_sku: str = ""
    retail_sku: str = ""
    purchase_quantity: Annotated[int, Field(gt=0, le=1000000)] = 1
    procurement_mode: ProcurementMode = "STOCK"
    min_order: Annotated[int, Field(gt=0, le=1000000)] | None = None
    dropship_verified: bool = False
    supply_pack: Annotated[int, Field(gt=0, le=100000)] | None = None
    retail_pack: Annotated[int, Field(gt=0, le=100000)] | None = None
    allow_repack: bool = False
    match_confirmed: bool = False
    verification_reason: str = Field(default="", max_length=3000)
    supply_attributes: dict[str, str] = Field(default_factory=dict)
    retail_attributes: dict[str, str] = Field(default_factory=dict)
    supply_price: Money | None = None
    retail_price: Money | None = None
    retail_shipping: Money | None = None
    sale_price: Money | None = None
    inbound_shipping: Money | None = None
    outbound_shipping: Money | None = None
    packaging: Money | None = None
    fees: Money | None = None
    fee_rate: Ratio = 0
    advertising: Money | None = None
    refund_rate: Ratio | None = None
    refund_loss: Money | None = None
    ad_budget: Money | None = None
    region: Text = "全国"

    @model_validator(mode="after")
    def verification(self):
        if self.match_confirmed and len(self.verification_reason.strip()) < 5:
            raise ValueError("确认同款需要至少5字的核验理由")
        if (self.supply_price is not None or self.retail_price is not None) and len(
            self.verification_reason
        ) < 5:
            raise ValueError("人工补充价格必须记录核验依据")
        return self


class PromoteInput(Contract):
    category: Text
    name: Text
