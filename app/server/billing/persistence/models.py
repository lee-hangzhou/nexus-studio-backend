from __future__ import annotations

from tortoise import fields
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.server.billing.domain.packs import CreditPackKey
from app.server.billing.domain.ports import BillingOrderRecord, OrderStatus
from app.server.persistence.model_base import AppendOnlyModel, BaseModel


class BillingOrder(BaseModel):
    user_id = fields.BigIntField(null=False)
    pack_key = fields.CharField(max_length=32, null=False)
    credits = fields.IntField(null=False)
    price_usd_cents = fields.IntField(null=False)
    status = fields.CharField(max_length=16, null=False, default=OrderStatus.PENDING.value)
    request_id = fields.CharField(max_length=128, null=False, unique=True)
    creem_product_id = fields.CharField(max_length=128, null=False)
    creem_checkout_id = fields.CharField(max_length=128, null=True)
    creem_order_id = fields.CharField(max_length=128, null=True)
    paid_at = fields.DatetimeField(null=True)

    class Meta(BaseModel.Meta):
        table = "billing_orders"
        abstract = False
        indexes = [
            ("user_id", "status"),
            ("creem_checkout_id",),
        ]


class BillingCreditBalance(BaseModel):
    user_id = fields.BigIntField(null=False, unique=True)
    balance = fields.BigIntField(null=False, default=0)
    version = fields.IntField(null=False, default=0)

    class Meta(BaseModel.Meta):
        table = "billing_credit_balances"
        abstract = False


class BillingCreditLedger(AppendOnlyModel):
    user_id = fields.BigIntField(null=False)
    order_id = fields.BigIntField(null=False)
    event_id = fields.CharField(max_length=128, null=False, unique=True)
    delta = fields.IntField(null=False)
    balance_after = fields.BigIntField(null=False)

    class Meta(AppendOnlyModel.Meta):
        table = "billing_credit_ledger"
        abstract = False
        indexes = [("user_id", "created_at")]


class BillingWebhookEvent(AppendOnlyModel):
    event_id = fields.CharField(max_length=128, null=False, unique=True)
    event_type = fields.CharField(max_length=64, null=False)

    class Meta(AppendOnlyModel.Meta):
        table = "billing_webhook_events"
        abstract = False


def _to_record(row: BillingOrder) -> BillingOrderRecord:
    """ORM 订单行转为领域记录"""
    return BillingOrderRecord(
        id=row.id,
        user_id=row.user_id,
        pack_key=CreditPackKey(row.pack_key),
        credits=row.credits,
        price_usd_cents=row.price_usd_cents,
        status=OrderStatus(row.status),
        request_id=row.request_id,
        creem_product_id=row.creem_product_id,
        creem_checkout_id=row.creem_checkout_id,
        creem_order_id=row.creem_order_id,
        paid_at=row.paid_at,
    )


class TortoiseBillingRepository:
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
        row = await BillingOrder.create(
            user_id=user_id,
            pack_key=pack_key.value,
            credits=credits,
            price_usd_cents=price_usd_cents,
            status=OrderStatus.PENDING.value,
            request_id=request_id,
            creem_product_id=creem_product_id,
        )
        return _to_record(row)

    async def attach_checkout(self, *, order_id: int, creem_checkout_id: str) -> None:
        """回填 Creem checkout id"""
        updated = await BillingOrder.filter(id=order_id).update(creem_checkout_id=creem_checkout_id)
        if updated != 1:
            raise LookupError(f"billing order not found: {order_id}")

    async def get_by_request_id(self, request_id: str) -> BillingOrderRecord | None:
        """按本地 request_id 查订单"""
        row = await BillingOrder.filter(request_id=request_id).first()
        return _to_record(row) if row else None

    async def get_by_creem_checkout_id(self, creem_checkout_id: str) -> BillingOrderRecord | None:
        """按 Creem checkout id 查订单"""
        row = await BillingOrder.filter(creem_checkout_id=creem_checkout_id).first()
        return _to_record(row) if row else None

    async def mark_paid(self, *, order_id: int, creem_order_id: str | None, paid_at) -> BillingOrderRecord:
        """将订单标为已支付（幂等）"""
        async with in_transaction():
            row = await BillingOrder.select_for_update().get(id=order_id)
            if row.status == OrderStatus.PAID.value:
                return _to_record(row)
            row.status = OrderStatus.PAID.value
            row.creem_order_id = creem_order_id
            row.paid_at = paid_at
            await row.save(update_fields=["status", "creem_order_id", "paid_at", "updated_at"])
            return _to_record(row)

    async def get_balance(self, user_id: int) -> int:
        """读取用户积分余额；无行视为 0"""
        row = await BillingCreditBalance.filter(user_id=user_id).first()
        return row.balance if row else 0

    async def credit(
        self,
        *,
        user_id: int,
        delta: int,
        order_id: int,
        event_id: str,
    ) -> int:
        """按 webhook event 幂等入账并返回余额"""
        if delta <= 0:
            raise ValueError("credit delta must be positive")
        async with in_transaction():
            existing_ledger = await BillingCreditLedger.filter(event_id=event_id).first()
            if existing_ledger is not None:
                return existing_ledger.balance_after

            row = await BillingCreditBalance.select_for_update().filter(user_id=user_id).first()
            if row is None:
                row = await BillingCreditBalance.create(user_id=user_id, balance=0, version=0)
                row = await BillingCreditBalance.select_for_update().get(id=row.id)

            row.balance = row.balance + delta
            row.version = row.version + 1
            await row.save(update_fields=["balance", "version", "updated_at"])
            await BillingCreditLedger.create(
                user_id=user_id,
                order_id=order_id,
                event_id=event_id,
                delta=delta,
                balance_after=row.balance,
            )
            return row.balance

    async def try_begin(self, event_id: str, event_type: str) -> bool:
        """尝试登记 webhook 事件；重复则 False"""
        existing = await BillingWebhookEvent.filter(event_id=event_id).first()
        if existing is not None:
            return False
        try:
            await BillingWebhookEvent.create(event_id=event_id, event_type=event_type)
        except IntegrityError:
            return False
        return True
