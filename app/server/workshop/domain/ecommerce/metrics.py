from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from app.contracts.ecommerce import ECOM_FORMULA_VERSION

FORMULA_VERSION = ECOM_FORMULA_VERSION


@dataclass(frozen=True, slots=True)
class MetricResult:
    value: float | None
    unit: str
    formula_version: str = FORMULA_VERSION
    unavailable_reason: str | None = None


def _ratio(numerator: float, denominator: float, *, unit: str) -> MetricResult:
    """安全除法得到比率；分母为 0 返回 None"""
    if denominator == 0:
        return MetricResult(value=None, unit=unit, unavailable_reason="zero_denominator")
    return MetricResult(value=numerator / denominator, unit=unit)


def net_paid_amount(*, paid_amount_fen: int, refund_amount_fen: int) -> MetricResult:
    """计算净实付金额"""
    return MetricResult(value=float(paid_amount_fen - refund_amount_fen), unit="fen")


def click_through_rate(*, clicks: int, impressions: int) -> MetricResult:
    """计算点击率"""
    return _ratio(float(clicks), float(impressions), unit="ratio")


def visitor_conversion_rate(*, paid_buyers: int, visitors: int) -> MetricResult:
    """计算访客转化率"""
    return _ratio(float(paid_buyers), float(visitors), unit="ratio")


def ad_conversion_rate(*, attributed_orders: int, clicks: int) -> MetricResult:
    """计算广告转化率"""
    return _ratio(float(attributed_orders), float(clicks), unit="ratio")


def average_order_value(*, net_paid_amount_fen: float, paid_orders: int) -> MetricResult:
    """计算客单价"""
    return _ratio(net_paid_amount_fen, float(paid_orders), unit="fen")


def ad_roas(*, attributed_revenue_fen: int, spend_fen: int) -> MetricResult:
    """计算广告 ROAS"""
    return _ratio(float(attributed_revenue_fen), float(spend_fen), unit="ratio")


def unit_variable_cost(
    *,
    purchase_cost_fen: int,
    fulfillment_cost_fen: int,
    other_variable_cost_fen: int,
) -> MetricResult:
    """计算单位变动成本"""
    total = purchase_cost_fen + fulfillment_cost_fen + other_variable_cost_fen
    return MetricResult(value=float(total), unit="fen")


def estimated_platform_fee(
    *,
    net_paid_amount_fen: float,
    platform_fee_rate_bps: int,
) -> MetricResult:
    """估算平台费用"""
    if net_paid_amount_fen == 0:
        return MetricResult(value=0.0, unit="fen")
    fee = (
        Decimal(str(net_paid_amount_fen))
        * Decimal(platform_fee_rate_bps)
        / Decimal(10000)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return MetricResult(value=float(fee), unit="fen")


def gross_profit_before_ads(
    *,
    net_paid_amount_fen: float,
    paid_quantity: int,
    unit_variable_cost_fen: float,
    estimated_platform_fee_fen: float,
) -> MetricResult:
    """计算广告前毛利"""
    value = net_paid_amount_fen - (paid_quantity * unit_variable_cost_fen) - estimated_platform_fee_fen
    return MetricResult(value=value, unit="fen")


def gross_margin_before_ads(*, gross_profit_fen: float, net_paid_amount_fen: float) -> MetricResult:
    """计算广告前毛利率"""
    return _ratio(gross_profit_fen, net_paid_amount_fen, unit="ratio")


def average_inventory_qty(*, opening_available_qty: int, closing_available_qty: int) -> MetricResult:
    """计算平均库存数量"""
    return MetricResult(value=(opening_available_qty + closing_available_qty) / 2.0, unit="units")


def inventory_turnover_qty(*, paid_quantity: int, average_inventory_qty: float) -> MetricResult:
    """计算库存周转次数"""
    return _ratio(float(paid_quantity), average_inventory_qty, unit="turns")


def days_of_inventory(
    *,
    closing_available_qty: int,
    paid_quantity: int,
    covered_calendar_days: int,
) -> MetricResult:
    """计算库存可售天数"""
    if covered_calendar_days <= 0:
        return MetricResult(
            value=None, unit="days", unavailable_reason="invalid_covered_days"
        )
    daily_rate = paid_quantity / covered_calendar_days
    return _ratio(float(closing_available_qty), daily_rate, unit="days")


def compute_all_ecom_v1_metrics(
    *,
    paid_amount_fen: int,
    refund_amount_fen: int,
    paid_orders: int,
    paid_quantity: int,
    purchase_cost_fen: int,
    fulfillment_cost_fen: int,
    other_variable_cost_fen: int,
    platform_fee_rate_bps: int,
    clicks: int = 0,
    impressions: int = 0,
    visitors: int = 0,
    paid_buyers: int = 0,
    attributed_orders: int = 0,
    attributed_revenue_fen: int = 0,
    spend_fen: int = 0,
    opening_available_qty: int = 0,
    closing_available_qty: int = 0,
    covered_calendar_days: int = 0,
) -> dict[str, MetricResult]:
    """批量计算 ecom-v1 公式集"""
    net = net_paid_amount(paid_amount_fen=paid_amount_fen, refund_amount_fen=refund_amount_fen)
    net_val = net.value if net.value is not None else 0.0
    uvc = unit_variable_cost(
        purchase_cost_fen=purchase_cost_fen,
        fulfillment_cost_fen=fulfillment_cost_fen,
        other_variable_cost_fen=other_variable_cost_fen,
    )
    uvc_val = uvc.value if uvc.value is not None else 0.0
    fee = estimated_platform_fee(
        net_paid_amount_fen=net_val,
        platform_fee_rate_bps=platform_fee_rate_bps,
    )
    fee_val = fee.value if fee.value is not None else 0.0
    gp = gross_profit_before_ads(
        net_paid_amount_fen=net_val,
        paid_quantity=paid_quantity,
        unit_variable_cost_fen=uvc_val,
        estimated_platform_fee_fen=fee_val,
    )
    gp_val = gp.value if gp.value is not None else 0.0
    avg_inv = average_inventory_qty(
        opening_available_qty=opening_available_qty,
        closing_available_qty=closing_available_qty,
    )
    avg_inv_val = avg_inv.value if avg_inv.value is not None else 0.0
    return {
        "net_paid_amount": net,
        "click_through_rate": click_through_rate(clicks=clicks, impressions=impressions),
        "visitor_conversion_rate": visitor_conversion_rate(
            paid_buyers=paid_buyers, visitors=visitors
        ),
        "ad_conversion_rate": ad_conversion_rate(
            attributed_orders=attributed_orders, clicks=clicks
        ),
        "average_order_value": average_order_value(
            net_paid_amount_fen=net_val, paid_orders=paid_orders
        ),
        "ad_roas": ad_roas(
            attributed_revenue_fen=attributed_revenue_fen, spend_fen=spend_fen
        ),
        "unit_variable_cost": uvc,
        "estimated_platform_fee": fee,
        "gross_profit_before_ads": gp,
        "gross_margin_before_ads": gross_margin_before_ads(
            gross_profit_fen=gp_val, net_paid_amount_fen=net_val
        ),
        "average_inventory_qty": avg_inv,
        "inventory_turnover_qty": inventory_turnover_qty(
            paid_quantity=paid_quantity, average_inventory_qty=avg_inv_val
        ),
        "days_of_inventory": days_of_inventory(
            closing_available_qty=closing_available_qty,
            paid_quantity=paid_quantity,
            covered_calendar_days=covered_calendar_days,
        ),
    }
