"""Deterministic calculations. No database, HTTP or model calls."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
import re

D = Decimal
WEIGHTS = {
    "demand": 20,
    "competition": 15,
    "profit": 25,
    "supply": 15,
    "growth": 10,
    "aftersale": 10,
    "differentiation": 5,
}
COST_FIELDS = [
    "purchase",
    "purchase_refund",
    "shipping",
    "packaging",
    "net_fees",
    "advertising",
    "other_net_cost",
]


def decimal(value):
    return None if value is None else D(str(value))


def money(value, places=4):
    return format(D(str(value)).quantize(D(10) ** -places, rounding=ROUND_HALF_UP), f".{places}f")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def profit(inputs):
    p, c, f, b, t, a, r, loss = (decimal(inputs[k]) for k in ["p", "c", "f", "b", "t", "a", "r", "l"])
    retained = p - c - f - b - t
    max_cac = (1 - r) * retained - r * loss
    contribution = max_cac - a
    return {
        "contribution": money(contribution),
        "max_cac": money(max_cac),
        "contribution_rate": float(contribution / p) if p > 0 else None,
        "display_contribution": money(contribution, 2),
    }


def order_contribution(data):
    if any(data.get(k) is None for k in COST_FIELDS):
        return None
    result = decimal(data["revenue"]) - decimal(data.get("customer_refund", "0"))
    result -= decimal(data["purchase"])
    result += decimal(data["purchase_refund"])
    for key in ["shipping", "packaging", "net_fees", "advertising", "other_net_cost"]:
        result -= decimal(data[key])
    return result


def ratio(a, b):
    return decimal(a) / decimal(b) if a is not None and b is not None and decimal(b) > 0 else None


def growth(current, previous):
    result = ratio(current, previous)
    return result - 1 if result is not None else None


def tri_all(values):
    return "FAIL" if "FAIL" in values else "UNKNOWN" if "UNKNOWN" in values else "PASS"


def tri_any(values):
    return "PASS" if "PASS" in values else "UNKNOWN" if "UNKNOWN" in values else "FAIL"


def check(value, condition):
    return "UNKNOWN" if value is None else "PASS" if condition(decimal(value)) else "FAIL"


def blue_ocean(k, config):
    c = config["strategy"]["blue_ocean"]
    return tri_all(
        [
            check(
                k.get("search_index"),
                lambda x: D(str(c["search_index_min"])) <= x <= D(str(c["search_index_max"])),
            ),
            check(k.get("listing_count"), lambda x: x < D(str(c["listing_count_max_exclusive"]))),
            check(
                ratio(k.get("search_index"), k.get("listing_count")),
                lambda x: x > D(str(c["opportunity_min_exclusive"])),
            ),
            check(k.get("conversion"), lambda x: x >= D(str(c["conversion_min"]))),
            check(k.get("mall_click_share"), lambda x: x < D(str(c["mall_click_share_max_exclusive"]))),
        ]
    )


def normalize(value, population, metric, config, inverse=False):
    if value is None:
        return None
    values = sorted(decimal(x) for x in population if x is not None)
    x = decimal(value)
    if len(values) >= config["score"]["min_percentile_sample"]:
        below = sum(v < x for v in values)
        equal = sum(v == x for v in values)
        rank = D(below) + (D(equal) + 1) / 2
        score = D(100) * (rank - 1) / D(len(values) - 1)
    else:
        limits = config["score"].get("fallback_ranges", {}).get(metric)
        if not limits:
            return None
        low, high = map(lambda v: D(str(v)), limits)
        if high <= low:
            return None
        score = max(D(0), min(D(100), D(100) * (x - low) / (high - low)))
    return D(100) - score if inverse else score


def weighted(parts):
    if any(score is None for score, _weight in parts):
        return None
    return sum(decimal(score) * D(str(weight)) for score, weight in parts)


def summarize_scores(scores, config, gate="PASS", critical_missing=False):
    weights = config["score"]["weights"]
    known = sum(weights[k] for k, value in scores.items() if value is not None)
    low = sum(decimal(value) * D(str(weights[k])) / 100 for k, value in scores.items() if value is not None)
    high = low + 100 - known
    coverage = D(known) / 100
    if gate != "BLOCKED" and (critical_missing or coverage < 1):
        gate = "NEEDS_EVIDENCE"
    grade = None
    if coverage == 1 and gate == "PASS":
        for name, threshold in sorted(
            config["score"]["grades"].items(), key=lambda pair: D(str(pair[1])), reverse=True
        ):
            if low >= D(str(threshold)):
                grade = name
                break
    return {
        "factors": {k: float(v) if v is not None else None for k, v in scores.items()},
        "coverage": float(coverage),
        "score_low": float(low),
        "score_high": float(high),
        "score": float(low) if coverage == 1 else None,
        "grade": grade,
        "gate": gate,
        "display_interval": coverage >= D(str(config["score"]["min_coverage_for_partial_display"])),
    }


def normalize_attribute(value):
    text = str(value).strip().lower().replace(" ", "")
    match = re.fullmatch(r"([\d.]+)(mm|cm|m|g|kg)", text)
    if match:
        number = D(match[1])
        unit = match[2]
        multiplier = {"mm": 1, "cm": 10, "m": 1000, "g": 1, "kg": 1000}[unit]
        return format((number * multiplier).normalize(), "f") + ("mm" if unit in ["mm", "cm", "m"] else "g")
    return text


def match_conflicts(sku, listing, required):
    conflicts, missing = [], []
    if sku["quantity"] != listing["quantity"]:
        conflicts.append("quantity")
    left, right = sku["attributes"], listing["attributes"]
    for name in set(left) | set(right) | set(required):
        a, b = left.get(name), right.get(name)
        if a and b and normalize_attribute(a) != normalize_attribute(b):
            conflicts.append(name)
        elif name in required and (not a or not b):
            missing.append(name)
    return conflicts, missing


def quantile(values, fraction):
    values = sorted(values)
    position = D(len(values) - 1) * D(str(fraction))
    left = int(position)
    right = min(left + 1, len(values) - 1)
    return values[left] + (values[right] - values[left]) * (position - left)


def distribute(total, keys):
    """Equal per paid order; deterministic remainder belongs to the last key."""
    keys = sorted(set(keys))
    if not keys:
        return {}
    part = (decimal(total) / len(keys)).quantize(D(".0001"), rounding=ROUND_HALF_UP)
    result = {key: part for key in keys}
    result[keys[-1]] += decimal(total) - sum(result.values())
    return result


def iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
