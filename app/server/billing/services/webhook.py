from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from tortoise.transactions import in_transaction

from app.server.billing.domain.grant import (
    CheckoutCompletedGrant,
    WebhookGrantError,
    parse_checkout_completed_grant,
)
from app.server.billing.domain.packs import CreditPackKey
from app.server.billing.domain.ports import (
    BillingOrderStore,
    CreditBalanceStore,
    OrderStatus,
    WebhookEventStore,
)
from app.server.billing.domain.webhook_signature import verify_creem_webhook_signature
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True, slots=True)
class WebhookApplyResult:
    status: Literal["applied", "duplicate", "ignored"]
    event_id: str | None
    credits_granted: int = 0
    balance_after: int | None = None


class BillingWebhookService:
    def __init__(
        self,
        *,
        webhook_secret: str,
        product_id_by_pack: dict[CreditPackKey, str],
        orders: BillingOrderStore,
        balances: CreditBalanceStore,
        events: WebhookEventStore,
        begin_transaction: TransactionFactory | None = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._product_id_by_pack = product_id_by_pack
        self._orders = orders
        self._balances = balances
        self._events = events
        self._begin_transaction = begin_transaction or in_transaction

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> None:
        """校验 Creem webhook HMAC；失败则拒绝"""
        if not verify_creem_webhook_signature(raw_body, signature_header, self._webhook_secret):
            raise AppError(ErrorCode.PERMISSION_DENIED, "invalid creem webhook signature")

    async def apply_payload(self, payload: dict[str, Any]) -> WebhookApplyResult:
        """应用 webhook：校验金额后幂等入账"""
        async with self._begin_transaction():
            return await self._apply_payload(payload)

    async def _apply_payload(self, payload: dict[str, Any]) -> WebhookApplyResult:
        try:
            grant = parse_checkout_completed_grant(
                payload,
                product_id_by_pack=self._product_id_by_pack,
            )
        except WebhookGrantError as exc:
            raise AppError(ErrorCode.INVALID_PARAMS, exc.args[0], details={"code": exc.code}) from exc

        if grant is None:
            event_id = payload.get("id") if isinstance(payload.get("id"), str) else None
            return WebhookApplyResult(status="ignored", event_id=event_id)

        order = await self._resolve_order(grant)
        if order.credits != grant.pack.credits or order.pack_key != grant.pack.key:
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "order credits do not match creem product pack",
                details={"order_id": order.id, "pack": grant.pack.key},
            )
        if grant.amount is None:
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "creem order amount is required",
                details={"order_id": order.id},
            )
        if grant.amount != order.price_usd_cents:
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "creem order amount does not match billing order",
                details={
                    "order_id": order.id,
                    "expected_usd_cents": order.price_usd_cents,
                    "actual_amount": grant.amount,
                },
            )
        if grant.currency is None or grant.currency.strip().upper() != "USD":
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "creem order currency must be USD",
                details={"order_id": order.id, "currency": grant.currency},
            )

        if order.status == OrderStatus.PAID:
            balance = await self._balances.get_balance(order.user_id)
            return WebhookApplyResult(
                status="duplicate",
                event_id=grant.event_id,
                balance_after=balance,
            )

        claimed = await self._events.try_begin(grant.event_id, "checkout.completed")
        if not claimed:
            balance = await self._balances.get_balance(order.user_id)
            return WebhookApplyResult(
                status="duplicate",
                event_id=grant.event_id,
                balance_after=balance,
            )

        paid_at = datetime.now(timezone.utc)
        await self._orders.mark_paid(
            order_id=order.id,
            creem_order_id=grant.creem_order_id,
            paid_at=paid_at,
        )
        balance_after = await self._balances.credit(
            user_id=order.user_id,
            delta=grant.pack.credits,
            order_id=order.id,
            event_id=grant.event_id,
        )
        return WebhookApplyResult(
            status="applied",
            event_id=grant.event_id,
            credits_granted=grant.pack.credits,
            balance_after=balance_after,
        )

    async def _resolve_order(self, grant: CheckoutCompletedGrant):
        if grant.request_id:
            order = await self._orders.get_by_request_id(grant.request_id)
            if order is not None:
                return order
        order = await self._orders.get_by_creem_checkout_id(grant.creem_checkout_id)
        if order is None:
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "billing order not found for creem checkout",
                details={"checkout_id": grant.creem_checkout_id, "request_id": grant.request_id},
            )
        return order
