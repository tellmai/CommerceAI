"""Comparison and unit economics; no network, fabricated costs or title-only matches."""

from decimal import Decimal as D

from app.db import now
from app.domain import decimal, iso, match_conflicts, money, normalize_attribute, profit, stock_batch
from app.errors import BusinessError


def variant(item, variant_id):
    if variant_id:
        selected = next((v for v in item["variants"] if v["id"] == variant_id), None)
        if selected is None:
            raise BusinessError("SKU_NOT_FOUND", "选择的SKU不属于该商品", 409)
        return selected
    if len(item["variants"]) == 1:
        return item["variants"][0]
    return {
        "price": item["price"] if not item["variants"] else None,
        "attributes": item["attributes"],
        "pack_count": item["pack_count"],
    }


def analyze(supply, retail, body, mode, connection_current=True, quote_hours=24):
    s, r = variant(supply, body.supply_sku), variant(retail, body.retail_sku)
    sa, ra = {**s["attributes"], **body.supply_attributes}, {**r["attributes"], **body.retail_attributes}
    # Preserve supplier-only properties in the snapshot; compare shared canonical specifications.
    keys = {"material", "size", "brand", "model", "type", "bristle_material", "handle_material"}
    sa, ra = {k: v for k, v in sa.items() if k in keys}, {k: v for k, v in ra.items() if k in keys}
    sp, rp = body.supply_pack or s.get("pack_count"), body.retail_pack or r.get("pack_count")
    conflicts, missing = match_conflicts(
        {"quantity": sp, "attributes": sa}, {"quantity": rp, "attributes": ra}, ["material", "size"]
    )
    if not sp or not rp:
        missing.append("包装件数")
    for label, item, selected, manual_pack, manual_attrs, requested_sku in [
        ("货源", supply, s, body.supply_pack, body.supply_attributes, body.supply_sku),
        ("零售", retail, r, body.retail_pack, body.retail_attributes, body.retail_sku),
    ]:
        if len(item["variants"]) > 1 and not requested_sku:
            missing.append(label + "具体SKU")
        if manual_pack is not None and selected.get("pack_count") not in (None, manual_pack):
            conflicts.append(label + "原包装件数")
        for key, value in selected["attributes"].items():
            if key in keys and value and key in manual_attrs:
                if normalize_attribute(value) != normalize_attribute(manual_attrs[key]):
                    conflicts.append(label + "原规格 " + key)
    repack = bool(body.allow_repack and sp and rp and sp != rp)
    if repack and conflicts == ["quantity"]:
        state = "UNVERIFIED"
    else:
        state = (
            "CONFLICT" if conflicts else "CONFIRMED" if body.match_confirmed and not missing else "UNVERIFIED"
        )
    if body.match_confirmed and (conflicts or missing):
        raise BusinessError(
            "SKU_CONFLICT", "规格冲突或缺失，不能确认同款", 409, {"conflicts": conflicts, "missing": missing}
        )
    supply_price = body.supply_price if body.supply_price is not None else s.get("price")
    used_tier = None
    if body.supply_price is None and not supply["variants"] and supply["price_tiers"]:
        applicable = [t for t in supply["price_tiers"] if t["min_quantity"] <= body.purchase_quantity]
        if applicable:
            used_tier = applicable[-1]
            supply_price = used_tier["price"]
        else:
            supply_price = None
    if body.procurement_mode == "DROPSHIP":
        used_tier, supply_price = None, body.supply_price
    retail_price = body.retail_price if body.retail_price is not None else r.get("price")
    unit_cost = D(str(supply_price)) * D(rp) / D(sp) if supply_price is not None and sp and rp else None
    retail_shipping = body.retail_shipping
    if retail_shipping is None and retail.get("shipping_region") == body.region:
        retail_shipping = decimal(retail.get("shipping"))
    eligible = retail.get("price_kind", "LIST") == "LIST" or retail.get("eligibility_verified")
    if not eligible:
        retail_price = None
    retail_total = (
        decimal(retail_price) + retail_shipping
        if retail_price is not None and retail_shipping is not None
        else None
    )
    sale_price = (
        body.sale_price
        if body.sale_price is not None
        else (retail_total if retail_total is not None else retail_price)
    )
    min_order = body.min_order or supply["min_order"]
    blocked, pending = [], []
    if not eligible:
        pending.append("竞品优惠资格未核验")
    if state != "CONFIRMED":
        (blocked if state == "CONFLICT" else pending).append(
            "同款规格尚未确认" if state != "CONFLICT" else "规格冲突"
        )
    if not min_order:
        pending.append("起订量未知")
    elif body.purchase_quantity < min_order:
        blocked.append("采购数量未达到起订量")
    if body.procurement_mode == "DROPSHIP" and (
        not body.dropship_verified or body.purchase_quantity != 1 or supply_price is None
    ):
        pending.append("请核验一件代发及单件起订条件，并补录专用代发报价")
    if repack:
        pending.append("拆包重组需要另行验证包装和履约")
        if body.purchase_quantity * sp < rp:
            blocked.append("采购数量不足以组成一个销售包装")
    if not supply["detail_loaded"] or not retail["detail_loaded"]:
        pending.append("商品详情未完整获取")
    if supply["currency"] != "CNY" or retail["currency"] != "CNY":
        blocked.append("当前测算只支持人民币，请勿直接混算币种")
        unit_cost, retail_price, sale_price, supply_price, retail_total = None, None, None, None, None
    if not connection_current:
        pending.append("平台连接已更新或停用，请重新查询")
    if any(
        (now() - iso(item["observed_at"])).total_seconds() > quote_hours * 3600 for item in [supply, retail]
    ):
        pending.append(f"报价采集已超过{quote_hours}小时，请重新查询")
    incoming = body.inbound_shipping
    dispatch, sellable, batch_cost = body.outbound_shipping, None, None
    if body.procurement_mode == "DROPSHIP":
        procurement_cost = unit_cost
        dispatch = incoming + dispatch if incoming is not None and dispatch is not None else None
    else:
        procurement_cost = None
        sellable = body.purchase_quantity * sp // rp if sp and rp else None
        if supply_price is not None and incoming is not None and sp and rp:
            sellable, batch_cost, procurement_cost = stock_batch(
                body.purchase_quantity, sp, rp, decimal(supply_price), incoming
            )
    raw_inputs = {
        "p": sale_price,
        "c": procurement_cost,
        "f": dispatch,
        "b": body.packaging,
        "t": body.fees,
        "a": body.advertising,
        "r": body.refund_rate,
        "l": body.refund_loss,
    }
    names = {
        "p": "预计售价",
        "c": "采购价或进货运费",
        "f": "每单发货运费",
        "b": "包材与服务费",
        "t": "平台及变动税费",
        "a": "每单广告费",
        "r": "售后概率",
        "l": "售后净损失",
    }
    incomplete = [names[k] for k, value in raw_inputs.items() if value is None]
    normal, stress, stress_inputs = None, None, None
    if not incomplete:
        exact = {k: str(v) for k, v in raw_inputs.items()}
        exact["fee_rate"] = str(body.fee_rate)
        normal = profit(exact)
        stress_inputs = {
            **exact,
            "p": str(D(exact["p"]) * D(".9")),
            "c": str(D(exact["c"]) * D("1.1")),
            "a": str(D(exact["a"]) * D("1.25")),
            "r": str(min(D(1), D(exact["r"]) + D(".05"))),
            "l": str(D(exact["l"]) * D("1.1")),
        }
        stress = profit(stress_inputs)
        if D(normal["contribution"]) <= 0:
            blocked.append("正常情景贡献非正")
        if D(stress["contribution"]) < 0:
            blocked.append("压力情景贡献为负")
    else:
        pending.append("费用不完整：" + "、".join(incomplete))
    if body.procurement_mode == "STOCK" and body.ad_budget is None:
        pending.append("备货广告预算未知，现金需求不完整")
    sticker_spread = (
        D(str(retail_price)) - unit_cost if retail_price is not None and unit_cost is not None else None
    )
    cash = None
    if supply_price is not None and incoming is not None and body.ad_budget is not None:
        cash = D(str(supply_price)) * body.purchase_quantity + incoming + body.ad_budget
    recommendation = "NOT_RECOMMENDED" if blocked else "NEEDS_VERIFICATION" if pending else "REVIEW_SAMPLE"
    cautions = [
        "本报告分析价格和经济性，成交需求、样品质量、资质与履约仍需核验。",
        "报价以供应商确认和实际成交条件为准。"
        if mode == "MANUAL"
        else "第三方接口仅覆盖其可访问商品；展示价不等于供应商最终报价或你的实际成交价。",
    ]
    if mode == "DEMO":
        recommendation = "DEMO_ONLY"
        cautions.insert(0, "全部价格和商品均为模拟数据，不能作为实际采购依据。")
    return {
        "algorithm_version": "sourcing-economics-1.1",
        "match_state": state,
        "conflicts": conflicts,
        "missing_attributes": sorted(set(missing)),
        "normal": normal,
        "stress": stress,
        "normal_inputs": {
            **{k: str(v) if v is not None else None for k, v in raw_inputs.items()},
            "fee_rate": str(body.fee_rate),
        },
        "stress_inputs": stress_inputs,
        "sticker_spread": money(sticker_spread) if sticker_spread is not None else None,
        "retail_total": money(retail_total) if retail_total is not None else None,
        "retail_shipping": money(retail_shipping) if retail_shipping is not None else None,
        "retail_eligible": bool(eligible),
        "sellable_units": sellable,
        "stock_batch_cost": money(batch_cost) if batch_cost is not None else None,
        "procurement_cost_per_sale_pack": money(procurement_cost) if procurement_cost is not None else None,
        "supply_unit_price": money(supply_price) if supply_price is not None else None,
        "retail_unit_price": money(retail_price) if retail_price is not None else None,
        "min_order": min_order,
        "used_tier": used_tier,
        "initial_cash": money(cash) if cash is not None else None,
        "cash_scope": "仅采购货款、进货运费与广告预算；仓储、包材备货和回款准备金另计",
        "supply_attributes": sa,
        "retail_attributes": ra,
        "supply_pack": sp,
        "retail_pack": rp,
        "recommendation": recommendation,
        "blocked": sorted(set(blocked)),
        "pending": sorted(set(pending)),
        "cautions": cautions,
    }
