from __future__ import annotations

import pytest

from app.server.workshop.domain.ecommerce.metrics import (
    ad_conversion_rate,
    ad_roas,
    average_order_value,
    click_through_rate,
    compute_all_ecom_v1_metrics,
    days_of_inventory,
    gross_margin_before_ads,
    inventory_turnover_qty,
    net_paid_amount,
    visitor_conversion_rate,
)


def test_net_paid_amount_basic() -> None:
    """净成交额 = 支付 - 退款"""
    result = net_paid_amount(paid_amount_fen=10000, refund_amount_fen=1500)
    assert result.value == 8500.0
    assert result.unit == "fen"


def test_zero_denominator_returns_unavailable_not_zero() -> None:
    """零分母返回 unavailable 而非 0"""
    ctr = click_through_rate(clicks=10, impressions=0)
    assert ctr.value is None
    assert ctr.unavailable_reason == "zero_denominator"

    vcr = visitor_conversion_rate(paid_buyers=5, visitors=0)
    assert vcr.value is None

    acr = ad_conversion_rate(attributed_orders=2, clicks=0)
    assert acr.value is None

    aov = average_order_value(net_paid_amount_fen=1000.0, paid_orders=0)
    assert aov.value is None

    roas = ad_roas(attributed_revenue_fen=5000, spend_fen=0)
    assert roas.value is None

    turnover = inventory_turnover_qty(paid_quantity=10, average_inventory_qty=0.0)
    assert turnover.value is None

    doi = days_of_inventory(
        closing_available_qty=100, paid_quantity=0, covered_calendar_days=7
    )
    assert doi.value is None


def test_gross_margin_before_ads_zero_net_paid() -> None:
    """净成交额为 0 时毛利率 unavailable"""
    margin = gross_margin_before_ads(gross_profit_fen=100.0, net_paid_amount_fen=0.0)
    assert margin.value is None


def test_compute_all_ecom_v1_metrics_golden_fixture() -> None:
    """合成夹具金标准可复现"""
    metrics = compute_all_ecom_v1_metrics(
        paid_amount_fen=100000,
        refund_amount_fen=5000,
        paid_orders=20,
        paid_quantity=40,
        purchase_cost_fen=3000,
        fulfillment_cost_fen=500,
        other_variable_cost_fen=200,
        platform_fee_rate_bps=500,
        clicks=200,
        impressions=10000,
        visitors=800,
        paid_buyers=18,
        attributed_orders=15,
        attributed_revenue_fen=60000,
        spend_fen=12000,
        opening_available_qty=100,
        closing_available_qty=80,
        covered_calendar_days=7,
    )
    assert metrics["net_paid_amount"].value == 95000.0
    assert metrics["click_through_rate"].value == pytest.approx(0.02)
    assert metrics["visitor_conversion_rate"].value == pytest.approx(18 / 800)
    assert metrics["ad_conversion_rate"].value == pytest.approx(15 / 200)
    assert metrics["average_order_value"].value == pytest.approx(95000.0 / 20)
    assert metrics["ad_roas"].value == pytest.approx(60000 / 12000)
    assert metrics["unit_variable_cost"].value == 3700.0
    assert metrics["estimated_platform_fee"].value == 4750.0
    assert metrics["gross_profit_before_ads"].value == pytest.approx(
        95000.0 - (40 * 3700.0) - 4750.0
    )
    assert metrics["average_inventory_qty"].value == 90.0
    assert metrics["inventory_turnover_qty"].value == pytest.approx(40 / 90.0)
    assert metrics["days_of_inventory"].value == pytest.approx(80 / (40 / 7))
