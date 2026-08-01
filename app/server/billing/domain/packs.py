from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CreditPackKey(StrEnum):
    USD_5 = "usd_5"
    USD_15 = "usd_15"
    USD_50 = "usd_50"
    USD_100 = "usd_100"


# 1 USD = 10 credits → credits = usd_cents // 10
CREDITS_PER_USD_CENT = 10
CREDITS_PER_USD = 100 // CREDITS_PER_USD_CENT


def credits_for_usd_cents(usd_cents: int) -> int:
    """按美分换算积分；须为整美元金额"""
    if usd_cents <= 0:
        raise ValueError("usd_cents must be positive")
    if usd_cents % 100 != 0:
        raise ValueError("usd_cents must be whole dollars for credit packs")
    return usd_cents // CREDITS_PER_USD_CENT


@dataclass(frozen=True, slots=True)
class CreditPack:
    """一次性积分包规格"""

    key: CreditPackKey
    price_usd_cents: int
    credits: int
    label: str


CREDIT_PACKS: dict[CreditPackKey, CreditPack] = {
    CreditPackKey.USD_5: CreditPack(
        key=CreditPackKey.USD_5,
        price_usd_cents=500,
        credits=credits_for_usd_cents(500),
        label="$5 / 50 credits",
    ),
    CreditPackKey.USD_15: CreditPack(
        key=CreditPackKey.USD_15,
        price_usd_cents=1500,
        credits=credits_for_usd_cents(1500),
        label="$15 / 150 credits",
    ),
    CreditPackKey.USD_50: CreditPack(
        key=CreditPackKey.USD_50,
        price_usd_cents=5000,
        credits=credits_for_usd_cents(5000),
        label="$50 / 500 credits",
    ),
    CreditPackKey.USD_100: CreditPack(
        key=CreditPackKey.USD_100,
        price_usd_cents=10000,
        credits=credits_for_usd_cents(10000),
        label="$100 / 1000 credits",
    ),
}


def get_pack(key: CreditPackKey | str) -> CreditPack:
    """按档位 key 取积分包"""
    pack_key = CreditPackKey(key)
    return CREDIT_PACKS[pack_key]


def get_pack_by_creem_product_id(
    creem_product_id: str,
    *,
    product_id_by_pack: dict[CreditPackKey, str],
) -> CreditPack:
    """按 Creem product id 反查积分包；未知则 LookupError"""
    normalized = creem_product_id.strip()
    if not normalized:
        raise LookupError("creem product id is empty")
    for pack_key, product_id in product_id_by_pack.items():
        if product_id and product_id == normalized:
            return CREDIT_PACKS[pack_key]
    raise LookupError(f"unknown creem product id: {normalized}")
