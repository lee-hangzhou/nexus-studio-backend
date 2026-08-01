from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from tortoise.transactions import in_transaction

from app.server.billing.domain.packs import CreditPackKey, get_pack
from app.server.billing.domain.ports import BillingOrderStore, CreemCheckoutPort
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]


@asynccontextmanager
async def _null_transaction():
    yield


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    order_id: int
    request_id: str
    pack_key: CreditPackKey
    credits: int
    price_usd_cents: int
    checkout_url: str


class BillingCheckoutService:
    def __init__(
        self,
        *,
        product_id_by_pack: dict[CreditPackKey, str],
        success_url: str,
        orders: BillingOrderStore,
        creem: CreemCheckoutPort,
        begin_transaction: TransactionFactory | None = None,
    ) -> None:
        self._product_id_by_pack = product_id_by_pack
        self._success_url = success_url
        self._orders = orders
        self._creem = creem
        self._begin_transaction = begin_transaction or in_transaction

    async def create_checkout(
        self,
        *,
        user_id: int,
        pack_key: CreditPackKey | str,
        customer_email: str | None,
    ) -> CheckoutSession:
        """创建 pending 订单并换取 Creem checkout_url"""
        async with self._begin_transaction():
            pack = get_pack(pack_key)
            product_id = self._product_id_by_pack.get(pack.key, "").strip()
            if not product_id:
                raise AppError(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    f"creem product id not configured for pack {pack.key}",
                )
            if not self._success_url.strip():
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "billing success url is not configured")

            request_id = f"billing_{user_id}_{pack.key}_{uuid4().hex}"
            order = await self._orders.create_pending(
                user_id=user_id,
                pack_key=pack.key,
                credits=pack.credits,
                price_usd_cents=pack.price_usd_cents,
                request_id=request_id,
                creem_product_id=product_id,
            )
            checkout_id, checkout_url = await self._creem.create_checkout(
                product_id=product_id,
                request_id=request_id,
                success_url=self._success_url,
                customer_email=customer_email,
                metadata={
                    "user_id": str(user_id),
                    "pack_key": pack.key.value,
                    "order_id": str(order.id),
                },
            )
            await self._orders.attach_checkout(order_id=order.id, creem_checkout_id=checkout_id)
            return CheckoutSession(
                order_id=order.id,
                request_id=request_id,
                pack_key=pack.key,
                credits=pack.credits,
                price_usd_cents=pack.price_usd_cents,
                checkout_url=checkout_url,
            )


# 供无 Tortoise 的单测注入
null_transaction = _null_transaction
