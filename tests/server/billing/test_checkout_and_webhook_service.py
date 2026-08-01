from __future__ import annotations

import pytest

from app.server.billing.domain.packs import CreditPackKey
from app.server.billing.domain.ports import InMemoryBillingStores
from app.server.billing.services.checkout import BillingCheckoutService, null_transaction
from app.server.billing.services.webhook import BillingWebhookService
from app.server.exceptions.base import AppError


PRODUCT_MAP = {
    CreditPackKey.USD_5: "prod_5",
    CreditPackKey.USD_15: "prod_15",
    CreditPackKey.USD_50: "prod_50",
    CreditPackKey.USD_100: "prod_100",
}


class FakeCreem:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_checkout(self, **kwargs):
        self.calls.append(kwargs)
        return "ch_test_1", "https://checkout.creem.io/ch_test_1"


@pytest.mark.asyncio
async def test_checkout_creates_pending_order_and_url() -> None:
    stores = InMemoryBillingStores()
    creem = FakeCreem()
    service = BillingCheckoutService(
        product_id_by_pack=PRODUCT_MAP,
        success_url="https://app.example/billing/success",
        orders=stores,
        creem=creem,
        begin_transaction=null_transaction,
    )
    session = await service.create_checkout(
        user_id=7,
        pack_key=CreditPackKey.USD_15,
        customer_email="u@example.com",
    )
    assert session.credits == 150
    assert session.checkout_url.endswith("ch_test_1")
    order = await stores.get_by_request_id(session.request_id)
    assert order is not None
    assert order.user_id == 7
    assert order.creem_checkout_id == "ch_test_1"
    assert creem.calls[0]["metadata"]["user_id"] == "7"


@pytest.mark.asyncio
async def test_webhook_grants_credits_once() -> None:
    stores = InMemoryBillingStores()
    creem = FakeCreem()
    checkout = BillingCheckoutService(
        product_id_by_pack=PRODUCT_MAP,
        success_url="https://app.example/billing/success",
        orders=stores,
        creem=creem,
        begin_transaction=null_transaction,
    )
    session = await checkout.create_checkout(
        user_id=7,
        pack_key=CreditPackKey.USD_5,
        customer_email=None,
    )
    webhook = BillingWebhookService(
        webhook_secret="whsec",
        product_id_by_pack=PRODUCT_MAP,
        orders=stores,
        balances=stores,
        events=stores,
        begin_transaction=null_transaction,
    )
    payload = {
        "id": "evt_once",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_test_1",
            "request_id": session.request_id,
            "product": {"id": "prod_5"},
            "order": {"id": "ord_1", "amount": 500, "currency": "USD"},
            "metadata": {"user_id": "7"},
        },
    }
    first = await webhook.apply_payload(payload)
    second = await webhook.apply_payload(payload)
    assert first.status == "applied"
    assert first.credits_granted == 50
    assert first.balance_after == 50
    assert second.status == "duplicate"
    assert await stores.get_balance(7) == 50


@pytest.mark.asyncio
async def test_webhook_rejects_amount_mismatch() -> None:
    stores = InMemoryBillingStores()
    checkout = BillingCheckoutService(
        product_id_by_pack=PRODUCT_MAP,
        success_url="https://app.example/billing/success",
        orders=stores,
        creem=FakeCreem(),
        begin_transaction=null_transaction,
    )
    session = await checkout.create_checkout(
        user_id=7,
        pack_key=CreditPackKey.USD_5,
        customer_email=None,
    )
    webhook = BillingWebhookService(
        webhook_secret="whsec",
        product_id_by_pack=PRODUCT_MAP,
        orders=stores,
        balances=stores,
        events=stores,
        begin_transaction=null_transaction,
    )
    with pytest.raises(AppError):
        await webhook.apply_payload(
            {
                "id": "evt_bad_amount",
                "eventType": "checkout.completed",
                "object": {
                    "id": "ch_test_1",
                    "request_id": session.request_id,
                    "product": {"id": "prod_5"},
                    "order": {"id": "ord_1", "amount": 1, "currency": "USD"},
                    "metadata": {},
                },
            }
        )
    assert await stores.get_balance(7) == 0


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature() -> None:
    stores = InMemoryBillingStores()
    webhook = BillingWebhookService(
        webhook_secret="whsec",
        product_id_by_pack=PRODUCT_MAP,
        orders=stores,
        balances=stores,
        events=stores,
        begin_transaction=null_transaction,
    )
    with pytest.raises(AppError):
        webhook.verify_signature(b"{}", "deadbeef")
