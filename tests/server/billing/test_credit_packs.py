from __future__ import annotations

import pytest

from app.server.billing.domain.packs import (
    CREDIT_PACKS,
    CreditPackKey,
    credits_for_usd_cents,
    get_pack,
    get_pack_by_creem_product_id,
)


def test_one_usd_equals_ten_credits() -> None:
    assert credits_for_usd_cents(100) == 10
    assert credits_for_usd_cents(500) == 50
    assert credits_for_usd_cents(1500) == 150
    assert credits_for_usd_cents(5000) == 500
    assert credits_for_usd_cents(10000) == 1000


def test_catalog_has_exactly_four_one_time_packs() -> None:
    assert list(CREDIT_PACKS.keys()) == [
        CreditPackKey.USD_5,
        CreditPackKey.USD_15,
        CreditPackKey.USD_50,
        CreditPackKey.USD_100,
    ]
    assert get_pack(CreditPackKey.USD_5).credits == 50
    assert get_pack(CreditPackKey.USD_15).credits == 150
    assert get_pack(CreditPackKey.USD_50).credits == 500
    assert get_pack(CreditPackKey.USD_100).credits == 1000


def test_unknown_creem_product_id_is_rejected() -> None:
    with pytest.raises(LookupError):
        get_pack_by_creem_product_id("prod_unknown", product_id_by_pack={})
