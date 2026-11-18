"""Pricing boundaries, volume scenarios and workspace-local recommendation sources."""

from decimal import Decimal

from app.opportunities import ForecastInput, forecast

EXAMPLE = {
    "price": "49.90",
    "c": "15",
    "f": "4",
    "b": "1",
    "t": "1",
    "a": "3",
    "r": 0.08,
    "l": "8",
    "target_margin": 0.20,
    "fixed_cost": "200",
    "traffic": {"days": 30, "daily_visits": 200, "low": 0.02, "base": 0.04, "high": 0.06},
}


def test_prices_cost_ceiling_and_three_scenario_totals():
    value = forecast(ForecastInput.model_validate(EXAMPLE))
    assert value.break_even_price == "24.9600"
    assert value.minimum_target_price == value.suggested_price == "31.8900"
    assert value.max_purchase_cost == "29.0900"
    assert value.unit_variable_cost == "22.9600"
    assert value.unit_contribution == "22.9480"
    assert value.break_even_orders == 9
    assert [scenario.orders for scenario in value.scenarios] == [120, 240, 360]
    base = value.scenarios[1]
    assert (base.paid_revenue, base.retained_revenue, base.variable_cost) == (
        "11976.0000",
        "11017.9200",
        "5510.4000",
    )
    assert base.contribution == "5507.5200" and base.after_fixed_cost == "5307.5200"


def test_pricing_reference_is_checked_against_the_target_floor():
    body = ForecastInput.model_validate({**EXAMPLE, "price": None})
    value = forecast(body, Decimal("31.889"), 5)
    assert value.pricing_status == "ABOVE_REFERENCE" and value.suggested_price is None
    assert not value.scenarios
    value = forecast(body, Decimal("34.999"), 5)
    assert value.pricing_status == "WITHIN_REFERENCE" and value.effective_price == "34.9900"


def test_unknown_cost_still_allows_sales_projection_and_zero_is_known():
    value = forecast(ForecastInput.model_validate({**EXAMPLE, "f": None}))
    assert value.suggested_price is None and value.missing == ["f"]
    assert value.scenarios[1].paid_revenue == "11976.0000"
    assert value.scenarios[1].variable_cost is None
    zero = forecast(ForecastInput.model_validate({**EXAMPLE, "f": "0", "price": "0"}))
    assert zero.missing == [] and zero.unit_contribution_rate is None
    assert zero.scenarios[1].paid_revenue == "0.0000"
    all_loss = forecast(ForecastInput.model_validate({**EXAMPLE, "r": 1}))
    assert all_loss.pricing_status == "UNATTAINABLE" and all_loss.break_even_price is None
    assert all_loss.scenarios[1].retained_revenue == "0.0000"


def test_recommendation_scope_baseline_and_validation(client):
    response = client.get("/api/v1/opportunities", params={"search": "模型工具整理架", "page_size": 1})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert "模型工具整理架" in item["name"]
    candidate = client.get("/api/v1/candidates/" + item["id"]).json()
    keyword = client.get("/api/v1/keywords/" + candidate["data"]["keyword_id"]).json()
    assert item["keyword"] == keyword["data"]["keyword"]
    assert item["level"] in {"PRIORITY", "TEST", "VERIFY", "BLOCKED"}
    params = {"candidate_id": item["id"]}
    response = client.get("/api/v1/opportunities/baseline", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["reference_count"] > 0
    assert response.json()["inputs"]["c"] == "15"
    projected = client.post("/api/v1/opportunities/forecast", json={**EXAMPLE, **params})
    assert projected.status_code == 200, projected.text
    assert projected.json()["reference_price"] is not None
    bad_rates = {**EXAMPLE["traffic"], "low": 0.9}
    assert (
        client.post("/api/v1/opportunities/forecast", json={**EXAMPLE, "traffic": bad_rates}).status_code
        == 422
    )
    client.headers["X-Workspace"] = "live"
    assert client.get("/api/v1/opportunities", params={"search": "模型工具整理架"}).json()["items"] == []
    assert client.get("/api/v1/opportunities/baseline", params=params).status_code == 404
    assert client.post("/api/v1/opportunities/forecast", json={**EXAMPLE, **params}).status_code == 404
    client.headers["X-Workspace"] = "public"
    assert client.get("/api/v1/opportunities").json()["items"] == []
    assert client.post("/api/v1/opportunities/forecast", json=EXAMPLE).status_code == 409
