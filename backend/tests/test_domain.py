from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.domain import (
    distribute,
    growth,
    match_conflicts,
    money,
    normalize,
    order_contribution,
    profit,
    ratio,
    safe_cell,
    summarize_scores,
    tri_all,
    tri_any,
)
from app.schemas import ProfitInput


@pytest.mark.parametrize(
    "p,r,l,a,expected,display",
    [
        ("39.9", ".08", "8", "6", "10.7480", "10.75"),
        ("34.9", ".08", "8", "6", "6.1480", "6.15"),
        ("39.9", ".15", "10", "6", "8.5650", "8.57"),
        ("34.9", ".15", "10", "10", ".3150", "0.32"),
        ("39.9", "0", "8", "6", "12.9000", "12.90"),
        ("39.9", "1", "8", "6", "-14.0000", "-14.00"),
    ],
)
def test_profit_benchmarks(p, r, l, a, expected, display):
    result = profit(dict(p=p, c="15", f="4", b="1", t="1", a=a, r=r, l=l))
    assert D(result["contribution"]) == D(expected)
    assert result["display_contribution"] == display


def test_zero_price_and_negative_margin():
    result = profit(dict(p="0", c="0", f="0", b="0", t="0", a="0", r="0", l="0"))
    assert result["contribution_rate"] is None
    assert result["contribution"] == "0.0000"
    assert money("-1.005", 2) == "-1.01"


@pytest.mark.parametrize(
    "field,value", [("p", -1), ("p", 39.9), ("r", 8), ("a", "-1"), ("p", "NaN"), ("p", "1.00001")]
)
def test_input_validation(field, value):
    data = dict(p="39.9", c="15", f="4", b="1", t="1", a="6", r=0.08, l="8")
    data[field] = value
    with pytest.raises(ValidationError):
        ProfitInput.model_validate(data)


def test_unknowns_and_tristate():
    assert ratio(10, 0) is None
    assert ratio(0, 10) == 0
    assert growth(10, 0) is None
    assert tri_all(["UNKNOWN", "FAIL"]) == "FAIL"
    assert tri_all(["UNKNOWN", "PASS"]) == "UNKNOWN"
    assert tri_any(["UNKNOWN", "PASS"]) == "PASS"


def test_percentile_ties_and_missing_ranges():
    config = {"score": {"min_percentile_sample": 30, "fallback_ranges": {}}}
    assert normalize(5, [5] * 30, "x", config) == 50
    assert normalize(5, [1, 2, 5], "x", config) is None
    config["score"]["fallback_ranges"]["x"] = [0, 10]
    assert normalize(12, [], "x", config) == 100
    assert normalize(2, [], "x", config, inverse=True) == 80


def test_partial_score_does_not_redistribute_missing_weight():
    config = {
        "score": {
            "weights": {"demand": 20, "profit": 80},
            "grades": {"S": 85, "A": 75, "B": 65, "C": 50, "D": 0},
            "min_coverage_for_partial_display": 0.8,
        }
    }
    result = summarize_scores({"demand": None, "profit": 80}, config)
    assert result["coverage"] == 0.8
    assert result["score_low"] == 64
    assert result["score_high"] == 84
    assert result["grade"] is None
    assert summarize_scores({"demand": 100, "profit": 100}, config, "BLOCKED")["grade"] is None
    # PostgreSQL JSONB reorders object keys; grade precedence must follow thresholds.
    config["score"]["grades"] = {"A": 75, "B": 65, "C": 50, "D": 0, "S": 85}
    assert summarize_scores({"demand": 100, "profit": 100}, config)["grade"] == "S"


def test_sku_units_and_quantity_conflict():
    left = {"quantity": 10, "attributes": {"material": "硅胶", "size": "10mm"}}
    right = {"quantity": 10, "attributes": {"material": "硅胶", "size": "1cm"}}
    assert match_conflicts(left, right, ["material", "size"]) == ([], [])
    right["quantity"] = 1
    assert "quantity" in match_conflicts(left, right, ["material", "size"])[0]
    right["attributes"]["material"] = "塑料"
    assert "material" in match_conflicts(left, right, ["material", "size"])[0]


def test_missing_cost_and_refunds():
    data = dict(
        revenue="39.9",
        customer_refund="5",
        purchase="15",
        purchase_refund="2",
        shipping="4",
        packaging="1",
        net_fees="1",
        advertising=None,
        other_net_cost="0",
    )
    assert order_contribution(data) is None
    data["advertising"] = "0"
    assert order_contribution(data) == D("15.9")


def test_advertising_rounding_and_export_escape():
    result = distribute("10", ["c", "a", "b"])
    assert result == {"a": D("3.3333"), "b": D("3.3333"), "c": D("3.3334")}
    assert sum(result.values()) == 10
    assert safe_cell(" =HYPERLINK(1)").startswith("'")
    assert safe_cell("正常标题") == "正常标题"
