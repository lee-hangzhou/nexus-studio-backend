from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from app.server.billing.domain.packs import CreditPackKey


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"


@dataclass(slots=True)
class BillingOrderRecord:
    id: int
    user_id: int
    pack_key: CreditPackKey
    credits: int
    price_usd_cents: int
    status: OrderStatus
    request_id: str
    creem_product_id: str
    creem_checkout_id: str | None = None
    creem_order_id: str | None = None
    paid_at: datetime | None = None


@dataclass(slots=True)
class CreditBalanceRecord:
    user_id: int
    balance: int
    version: int = 0


class BillingOrderStore(Protocol):
    async def create_pending(
        self,
        *,
        user_id: int,
        pack_key: CreditPackKey,
        credits: int,
        price_usd_cents: int,
        request_id: str,
        creem_product_id: str,
    ) -> BillingOrderRecord: ...

    async def attach_checkout(self, *, order_id: int, creem_checkout_id: str) -> None: ...

    async def get_by_request_id(self, request_id: str) -> BillingOrderRecord | None: ...

    async def get_by_creem_checkout_id(self, creem_checkout_id: str) -> BillingOrderRecord | None: ...

    async def mark_paid(
        self,
        *,
        order_id: int,
        creem_order_id: str | None,
        paid_at: datetime,
    ) -> BillingOrderRecord: ...


class CreditBalanceStore(Protocol):
    async def get_balance(self, user_id: int) -> int: ...

    async def credit(
        self,
        *,
        user_id: int,
        delta: int,
        order_id: int,
        event_id: str,
    ) -> int: ...


class WebhookEventStore(Protocol):
    async def try_begin(self, event_id: str, event_type: str) -> bool:
        """若本事件首次认领返回 True；已处理过返回 False"""
        ...


class CreemCheckoutPort(Protocol):
    async def create_checkout(
        self,
        *,
        product_id: str,
        request_id: str,
        success_url: str,
        customer_email: str | None,
        metadata: dict[str, str],
    ) -> tuple[str, str]:
        """返回 (checkout_id, checkout_url)"""
        ...


@dataclass
class InMemoryBillingStores:
    """测试用内存账单存储"""

    orders: dict[int, BillingOrderRecord] = field(default_factory=dict)
    by_request: dict[str, int] = field(default_factory=dict)
    by_checkout: dict[str, int] = field(default_factory=dict)
    balances: dict[int, CreditBalanceRecord] = field(default_factory=dict)
    events: set[str] = field(default_factory=set)
    _next_order_id: int = 1

    async def create_pending(
        self,
        *,
        user_id: int,
        pack_key: CreditPackKey,
        credits: int,
        price_usd_cents: int,
        request_id: str,
        creem_product_id: str,
    ) -> BillingOrderRecord:
        """创建 pending 订单"""
        order_id = self._next_order_id
        self._next_order_id += 1
        record = BillingOrderRecord(
            id=order_id,
            user_id=user_id,
            pack_key=pack_key,
            credits=credits,
            price_usd_cents=price_usd_cents,
            status=OrderStatus.PENDING,
            request_id=request_id,
            creem_product_id=creem_product_id,
        )
        self.orders[order_id] = record
        self.by_request[request_id] = order_id
        return record

    async def attach_checkout(self, *, order_id: int, creem_checkout_id: str) -> None:
        """回填 Creem checkout id"""
        record = self.orders[order_id]
        record.creem_checkout_id = creem_checkout_id
        self.by_checkout[creem_checkout_id] = order_id

    async def get_by_request_id(self, request_id: str) -> BillingOrderRecord | None:
        """按 request_id 查订单"""
        order_id = self.by_request.get(request_id)
        return self.orders.get(order_id) if order_id is not None else None

    async def get_by_creem_checkout_id(self, creem_checkout_id: str) -> BillingOrderRecord | None:
        """按 Creem checkout id 查订单"""
        order_id = self.by_checkout.get(creem_checkout_id)
        return self.orders.get(order_id) if order_id is not None else None

    async def mark_paid(
        self,
        *,
        order_id: int,
        creem_order_id: str | None,
        paid_at: datetime,
    ) -> BillingOrderRecord:
        """将订单标为已支付"""
        record = self.orders[order_id]
        if record.status == OrderStatus.PAID:
            return record
        record.status = OrderStatus.PAID
        record.creem_order_id = creem_order_id
        record.paid_at = paid_at
        return record

    async def get_balance(self, user_id: int) -> int:
        """读取余额"""
        bal = self.balances.get(user_id)
        return bal.balance if bal else 0

    async def credit(
        self,
        *,
        user_id: int,
        delta: int,
        order_id: int,
        event_id: str,
    ) -> int:
        """入账并返回余额"""
        del order_id, event_id
        bal = self.balances.get(user_id)
        if bal is None:
            bal = CreditBalanceRecord(user_id=user_id, balance=0, version=0)
            self.balances[user_id] = bal
        bal.balance += delta
        bal.version += 1
        return bal.balance

    async def try_begin(self, event_id: str, event_type: str) -> bool:
        """首次认领事件返回 True"""
        del event_type
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True
