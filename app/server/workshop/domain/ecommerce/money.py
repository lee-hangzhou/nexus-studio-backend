from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.server.workshop.domain.ecommerce.metrics import MetricResult

# UniversalBP / 万相台 fields that must never auto-convert to fen.
UNIT_UNVERIFIED_AMOUNT_FIELDS: frozenset[str] = frozenset(
    {
        "charge",
        "total_charge",
        "crowd_scene_charge",
        "item_scene_charge",
        "activity_scene_charge",
        "display_charge",
        "search_charge",
        "content_scene_charge",
        "shop_scene_charge",
        "ecpc",
        "ecpm",
        "alipay_dir_amt",
        "alipay_indir_amt",
        "alipay_inshop_amt",
        "prepay_inshop_amt",
        "prepay_dir_amt",
        "prepay_indir_amt",
        "gmv_inshop_amt",
        "alipay_inshop_cost",
        "col_cart_cost",
        "item_col_cart_cost",
        "item_col_inshop_cost",
        "shop_col_inshop_cost",
        "cart_cost",
        "shopping_amt",
        "hy_pay_amt",
        "natural_pay_amt",
        "alipay_inshop_amt_avg",
    }
)


class MoneyParseError(Exception):
    """金额解析失败"""

    def __init__(self, code: str, message: str) -> None:
        """初始化"""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class UnitUnverifiedAmount:
    """万相台等未验证单位金额：只保留 raw_string"""

    field: str
    raw_string: str
    unit_status: str = "UNIT_UNVERIFIED"


def parse_top_yuan_to_fen(value: str | None) -> int:
    """TOP 交易/退款金额：元字符串 → fen（Decimal，禁止 float）"""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise MoneyParseError("TAOBAO_MONEY_UNAVAILABLE", "TOP money field empty")
    if isinstance(value, float):
        raise MoneyParseError("TAOBAO_MONEY_INVALID", "float money is forbidden")
    text = value.strip() if isinstance(value, str) else str(value)
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyParseError(
            "TAOBAO_MONEY_INVALID", f"invalid TOP yuan string: {text!r}"
        ) from exc
    if not amount.is_finite():
        raise MoneyParseError("TAOBAO_MONEY_INVALID", f"non-finite TOP yuan: {text!r}")
    scaled = amount * Decimal(100)
    fen = scaled.quantize(Decimal("1"))
    if fen != scaled:
        raise MoneyParseError(
            "TAOBAO_MONEY_INVALID",
            f"TOP yuan must have at most 2 decimal places: {text!r}",
        )
    return int(fen)


def preserve_unit_unverified_amount(field: str, raw: str | None) -> UnitUnverifiedAmount:
    """保存 UNIT_UNVERIFIED 金额原串；禁止转 fen"""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raise MoneyParseError("TAOBAO_MONEY_UNAVAILABLE", f"{field} raw amount empty")
    return UnitUnverifiedAmount(field=field, raw_string=str(raw).strip())


def ad_roas_from_verified_fen(
    *,
    attributed_revenue_fen: int | None,
    spend_fen: int | None,
    spend_raw: UnitUnverifiedAmount | None = None,
    revenue_raw: UnitUnverifiedAmount | None = None,
) -> MetricResult:
    """仅当双方均为已验证 fen 时计算 ROAS；UNIT_UNVERIFIED 不得进入计算"""
    if spend_raw is not None or revenue_raw is not None:
        return MetricResult(
            value=None,
            unit="ratio",
            unavailable_reason="unit_unverified_amount",
        )
    if attributed_revenue_fen is None or spend_fen is None:
        return MetricResult(
            value=None,
            unit="ratio",
            unavailable_reason="unit_unverified_amount",
        )
    if spend_fen == 0:
        return MetricResult(value=None, unit="ratio", unavailable_reason="zero_denominator")
    return MetricResult(
        value=float(attributed_revenue_fen) / float(spend_fen),
        unit="ratio",
    )


def is_unit_unverified_field(field: str) -> bool:
    """判断字段是否 UNIT_UNVERIFIED"""
    return field in UNIT_UNVERIFIED_AMOUNT_FIELDS


def extract_unit_unverified_from_row(row: dict[str, Any]) -> dict[str, UnitUnverifiedAmount]:
    """从万相台行提取 UNIT_UNVERIFIED 字段为 raw；不产生 fen"""
    out: dict[str, UnitUnverifiedAmount] = {}
    for field in UNIT_UNVERIFIED_AMOUNT_FIELDS:
        if field not in row:
            continue
        value = row[field]
        if value is None or value == "":
            continue
        out[field] = UnitUnverifiedAmount(field=field, raw_string=str(value))
    return out
