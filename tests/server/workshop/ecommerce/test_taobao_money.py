from __future__ import annotations

import pytest

from app.server.workshop.domain.ecommerce.money import (
    MoneyParseError,
    UnitUnverifiedAmount,
    ad_roas_from_verified_fen,
    parse_top_yuan_to_fen,
    preserve_unit_unverified_amount,
)


def test_top_yuan_string_200_07_to_20007_fen() -> None:
    """覆盖 top yuan string 200 07 to 20007 fen"""
    assert parse_top_yuan_to_fen("200.07") == 20007


def test_top_yuan_string_0_01_to_1_fen() -> None:
    """覆盖 top yuan string 0 01 to 1 fen"""
    assert parse_top_yuan_to_fen("0.01") == 1


def test_top_yuan_rejects_illegal_format() -> None:
    """覆盖 top yuan rejects illegal format"""
    with pytest.raises(MoneyParseError) as exc:
        parse_top_yuan_to_fen("abc")
    assert exc.value.code == "TAOBAO_MONEY_INVALID"


def test_top_yuan_empty_is_contractual_unavailable() -> None:
    """覆盖 top yuan empty is contractual unavailable"""
    with pytest.raises(MoneyParseError) as exc:
        parse_top_yuan_to_fen(None)
    assert exc.value.code == "TAOBAO_MONEY_UNAVAILABLE"
    with pytest.raises(MoneyParseError) as exc2:
        parse_top_yuan_to_fen("")
    assert exc2.value.code == "TAOBAO_MONEY_UNAVAILABLE"


def test_universalbp_raw_amount_not_converted_to_fen() -> None:
    """覆盖 universalbp raw amount not converted to fen"""
    raw = preserve_unit_unverified_amount("charge", "12.34")
    assert isinstance(raw, UnitUnverifiedAmount)
    assert raw.raw_string == "12.34"
    assert not hasattr(raw, "fen") or getattr(raw, "fen", None) is None
    assert raw.unit_status == "UNIT_UNVERIFIED"


def test_unit_unverified_excluded_from_roas_spend() -> None:
    """覆盖 unit unverified excluded from roas spend"""
    result = ad_roas_from_verified_fen(
        attributed_revenue_fen=None,
        spend_fen=None,
        spend_raw=UnitUnverifiedAmount(field="charge", raw_string="100.00"),
        revenue_raw=UnitUnverifiedAmount(field="alipay_inshop_amt", raw_string="500.00"),
    )
    assert result.value is None
    assert result.unavailable_reason == "unit_unverified_amount"
