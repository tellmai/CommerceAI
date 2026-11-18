"""Bounded HTTPS adapter for the documented OneBound search/detail interfaces.

Provider examples are not live data. List prices, SKU prices, group prices and
volume tiers remain distinct, and missing shipping never becomes zero.
"""

from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.db import now
from app.domain import money
from app.errors import BusinessError

# HTTPX logs full request URLs at INFO; this provider requires keys in query parameters.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

HOSTS = {
    "1688": ["1688.com"],
    "taobao": ["taobao.com", "tmall.com"],
    "jd": ["jd.com"],
    "pinduoduo": ["yangkeduo.com", "pinduoduo.com"],
}
ATTRS = {
    "材质": "material",
    "刷毛材质": "bristle_material",
    "刷柄材质": "handle_material",
    "尺寸": "size",
    "规格尺寸": "size",
    "品牌": "brand",
    "型号": "model",
    "包装数量": "pack_count",
    "material": "material",
    "size": "size",
    "brand": "brand",
    "model": "model",
}


def number(value):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
        return result if result.is_finite() and 0 <= result < Decimal("1000000000") else None
    except InvalidOperation:
        return None


def positive_int(value):
    result = number(value)
    return int(result) if result is not None and result > 0 and result == int(result) else None


def first(*values):
    return next((v for v in values if v is not None and v != ""), None)


def safe_url(value, platform):
    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return ""
    if port not in {None, 80, 443} or "\\" in value:
        return ""
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        return ""
    if not any(host == h or host.endswith("." + h) for h in HOSTS[platform]):
        return ""
    # Provider query credentials must never enter saved quotes or exports.
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not re.search("key|secret|token|auth", k, re.I)]
    return urlunsplit(("https", host, parsed.path, urlencode(query), ""))


def fallback_url(platform, item_id):
    if not re.fullmatch(r"\d{1,30}", item_id):
        return ""
    return {
        "1688": f"https://detail.1688.com/offer/{item_id}.html",
        "taobao": f"https://item.taobao.com/item.htm?id={item_id}",
        "jd": f"https://item.jd.com/{item_id}.html",
        "pinduoduo": f"https://mobile.yangkeduo.com/goods.html?goods_id={item_id}",
    }[platform]


def attributes(raw):
    result = {}
    source = raw.get("props", {})
    if isinstance(source, list):
        for prop in source:
            if isinstance(prop, dict):
                if "name" in prop and "value" in prop:
                    result[str(prop["name"])] = str(prop["value"])
                else:
                    result.update(
                        {str(k): str(v) for k, v in prop.items() if not isinstance(v, (dict, list))}
                    )
    elif isinstance(source, dict):
        result.update({str(k): str(v) for k, v in source.items() if not isinstance(v, (dict, list))})
    extra = raw.get("attributes", {})
    if isinstance(extra, dict):
        result.update({str(k): str(v) for k, v in extra.items() if v is not None})
    props_name = str(raw.get("properties_name") or raw.get("props_name") or "")
    for piece in props_name.split(";"):
        parts = piece.split(":")
        if len(parts) >= 2:
            result[parts[-2]] = parts[-1]
    return {ATTRS.get(k, k)[:100]: v[:500] for k, v in list(result.items())[:50] if v.strip()}


def price_value(raw):
    value = number(raw.get("price"))
    if value is not None:
        return money(value)
    return None


def tiers(raw):
    values = first(raw.get("price_range"), raw.get("price_ranges"), [])
    if not isinstance(values, list):
        return []
    result = []
    for row in values[:100]:
        if not isinstance(row, dict):
            continue
        quantity = positive_int(first(row.get("begin"), row.get("beginAmount"), row.get("start_quantity")))
        price = number(row.get("price"))
        if quantity and price is not None:
            result.append({"min_quantity": quantity, "price": money(price)})
    return sorted(result, key=lambda r: r["min_quantity"])


def normalize_item(platform, raw, detail=False, requested_id=None):
    item_id = str(first(raw.get("num_iid"), raw.get("goods_id"), raw.get("item_id"), raw.get("id")) or "")
    if not item_id:
        raise BusinessError("PROVIDER_SCHEMA", "数据服务返回的商品缺少标识", 502)
    title = str(first(raw.get("title"), raw.get("goods_name"), "未提供标题"))[:500]
    attrs = attributes(raw)
    sku_data = raw.get("skus", [])
    if isinstance(sku_data, dict):
        sku_data = sku_data.get("sku", [])
    if not isinstance(sku_data, list):
        sku_data = []
    variants = []
    for index, sku in enumerate(sku_data[:100]):
        if not isinstance(sku, dict):
            continue
        sku_id = str(first(sku.get("sku_id"), sku.get("skuId"), index))
        sku_price = price_value(sku)
        warning = None
        # OneBound's JD documentation says other SKU prices repeat the requested SKU price.
        if platform == "jd" and sku_id != str(requested_id or item_id):
            sku_price, warning = None, "其他京东SKU价格需单独查询，未采用服务商重复价格"
        variants.append(
            {
                "id": sku_id,
                "label": str(first(sku.get("properties_name"), sku.get("properties"), sku_id))[:700],
                "price": sku_price,
                "attributes": {**attrs, **attributes(sku)},
                "pack_count": positive_int(first(sku.get("pack_count"), attrs.get("pack_count"))),
                "warning": warning,
            }
        )
    seller = raw.get("seller_info") if isinstance(raw.get("seller_info"), dict) else {}
    postage = number(first(raw.get("post_fee"), raw.get("freight"), raw.get("shipping")))
    pack = positive_int(first(raw.get("pack_count"), raw.get("package_count"), attrs.get("pack_count")))
    price = price_value(raw)
    warnings = []
    if not detail:
        warnings.append("列表展示价，需读取详情核验具体SKU")
    if not pack:
        warnings.append("包装件数未知，不能只按标题认定同款")
    if postage is None:
        warnings.append("运费未知")
    if raw.get("minGroupPrice") is not None or raw.get("min_group_price") is not None:
        warnings.append("包含拼团价格，需核验适用条件")
    if raw.get("promotion_price") not in [None, "", raw.get("price")]:
        warnings.append("促销价资格未核验，默认未采用")
    if price is None:
        warnings.append("无确定单一价格，需核验价格区间或SKU")
    normalized = {
        "id": platform + ":" + item_id,
        "external_id": item_id,
        "platform": platform,
        "title": title,
        "url": safe_url(str(raw.get("detail_url") or raw.get("url") or ""), platform)
        or fallback_url(platform, item_id),
        "price": price,
        "display_price": str(raw.get("price") or "")[:100],
        "promotion_price": str(raw.get("promotion_price"))
        if raw.get("promotion_price") is not None
        else None,
        "currency": str(raw.get("currency") or "CNY"),
        "currency_inferred": not bool(raw.get("currency")),
        "pack_count": pack,
        "attributes": attrs,
        "variants": variants,
        "shipping": money(postage) if postage is not None else None,
        "shipping_region": str(raw.get("shipping_region") or ""),
        "min_order": positive_int(first(raw.get("min_order"), raw.get("min_num"), raw.get("begin_amount"))),
        "price_tiers": tiers(raw),
        "seller": str(
            first(seller.get("shop_name"), seller.get("nick"), raw.get("seller_nick"), raw.get("nick")) or ""
        )[:500],
        "seller_id": str(first(seller.get("user_num_id"), seller.get("sid"), raw.get("seller_id")) or ""),
        "dropship": raw.get("dropship") if isinstance(raw.get("dropship"), bool) else None,
        "observed_at": now().isoformat(),
        "source_updated_at": None,
        "detail_loaded": detail,
        "warnings": warnings,
    }
    return normalized


def result_items(payload):
    container = first(payload.get("items"), payload.get("item"))
    if isinstance(container, dict):
        items = first(container.get("item"), container.get("items"))
        if items is None and str(container.get("total_results", "")) == "0":
            return []
        container = items
    if not isinstance(container, list):
        raise BusinessError("PROVIDER_SCHEMA", "商品数据服务未返回可识别的商品列表", 502)
    return container


class OneBound:
    def __init__(self, connection, transport=None):
        self.connection = connection
        self.client = httpx.Client(
            transport=transport,
            timeout=15,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1),
            trust_env=False,
        )
        self.calls = 0

    def close(self):
        self.client.close()

    def redact(self, value):
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            for key in ["api_key", "api_secret", "access_token"]:
                secret = self.connection.get(key)
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    def call(self, platform, method, **params):
        route = self.connection.get("taobao_route", "tglobal") if platform == "taobao" else platform
        if route not in {"1688", "taobao", "tglobal", "jd", "pinduoduo"} or method not in {
            "item_search",
            "item_get",
        }:
            raise BusinessError("INVALID_PROVIDER_ROUTE", "不支持此平台接口")
        query = {
            "key": self.connection["api_key"],
            "secret": self.connection.get("api_secret", ""),
            "cache": "no",
            "result_type": "json",
            **params,
        }
        if platform == "taobao" and self.connection.get("access_token"):
            query["access_token"] = self.connection["access_token"]
        self.calls += 1
        try:
            with self.client.stream(
                "GET", f"https://api-gw.onebound.cn/{route}/{method}/", params=query
            ) as response:
                if response.status_code != 200:
                    raise BusinessError(
                        "PROVIDER_HTTP", f"商品数据服务HTTP {response.status_code}，请检查平台权限与配额", 502
                    )
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 3 * 1024 * 1024:
                        raise BusinessError("PROVIDER_TOO_LARGE", "商品接口响应超过3MB限制", 502)
                    chunks.append(chunk)
                raw = b"".join(chunks)
            payload = json.loads(raw, parse_float=Decimal)
        except (httpx.HTTPError, ValueError):
            # HTTP exceptions include query URLs containing provider keys; never expose their text.
            raise BusinessError(
                "PROVIDER_UNAVAILABLE", "商品数据服务连接失败或响应无效，请检查服务状态", 502
            ) from None
        if not isinstance(payload, dict):
            raise BusinessError("PROVIDER_SCHEMA", "商品数据服务返回结构不正确", 502)
        code = str(payload.get("error_code", "0000"))
        if (
            code not in {"0", "0000", "", "None"}
            or payload.get("error")
            or payload.get("success") in [0, False]
        ):
            raise BusinessError(
                "PROVIDER_REJECTED", "商品接口拒绝请求，请核验Key、平台权限、配额及淘宝授权", 502
            )
        return payload, hashlib.sha256(raw).hexdigest()

    def search(self, platform, query, page, limit):
        payload, digest = self.call(
            platform, "item_search", q=query, page=page, page_size=min(20, max(5, limit))
        )
        items, failures, seen = [], 0, set()
        for raw in result_items(payload):
            if len(items) >= limit:
                break
            try:
                if not isinstance(raw, dict):
                    raise ValueError("invalid quote")
                item = self.redact(normalize_item(platform, raw))
                if item["id"] not in seen:
                    items.append(item)
                    seen.add(item["id"])
            except (BusinessError, TypeError, ValueError):
                failures += 1
        if failures and not items:
            raise BusinessError("PROVIDER_SCHEMA", "平台返回的商品缺少有效报价标识", 502)
        return items, {
            "response_sha256": digest,
            "discarded": failures,
            "provider_request_id": self.redact(str(payload.get("request_id", ""))[:100]),
        }

    def detail(self, platform, item_id):
        payload, digest = self.call(platform, "item_get", num_iid=item_id)
        item = payload.get("item")
        if isinstance(item, list):
            item = item[0] if item else None
        if not isinstance(item, dict):
            raise BusinessError("PROVIDER_SCHEMA", "商品详情不可用，列表价格保留待核验", 502)
        result = normalize_item(platform, item, detail=True, requested_id=item_id)
        result["response_sha256"] = digest
        return self.redact(result)
