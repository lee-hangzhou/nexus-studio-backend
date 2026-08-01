from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.server.billing.domain.packs import CreditPack, CreditPackKey, get_pack_by_creem_product_id


class WebhookGrantError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CheckoutCompletedGrant:
    event_id: str
    creem_checkout_id: str
    creem_order_id: str | None
    creem_product_id: str
    request_id: str | None
    amount: int | None
    currency: str | None
    metadata: dict[str, Any]
    pack: CreditPack


def parse_checkout_completed_grant(
    payload: dict[str, Any],
    *,
    product_id_by_pack: dict[CreditPackKey, str],
) -> CheckoutCompletedGrant | None:
    """解析 Creem webhook；非 checkout.completed 返回 None"""
    event_type = payload.get("eventType")
    if event_type is None:
        raise WebhookGrantError("WEBHOOK_MALFORMED", "missing eventType")
    if event_type != "checkout.completed":
        return None

    event_id = payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise WebhookGrantError("WEBHOOK_MALFORMED", "missing event id")

    obj = payload.get("object")
    if not isinstance(obj, dict):
        raise WebhookGrantError("WEBHOOK_MALFORMED", "missing checkout object")

    checkout_id = obj.get("id")
    if not isinstance(checkout_id, str) or not checkout_id.strip():
        raise WebhookGrantError("WEBHOOK_MALFORMED", "missing checkout id")

    product = obj.get("product")
    product_id: str | None = None
    if isinstance(product, dict):
        raw_pid = product.get("id")
        if isinstance(raw_pid, str):
            product_id = raw_pid
    elif isinstance(product, str):
        product_id = product

    order = obj.get("order")
    order_id: str | None = None
    amount: int | None = None
    currency: str | None = None
    if isinstance(order, dict):
        raw_oid = order.get("id")
        if isinstance(raw_oid, str):
            order_id = raw_oid
        if isinstance(order.get("amount"), int):
            amount = order["amount"]
        if isinstance(order.get("currency"), str):
            currency = order["currency"]
        if product_id is None:
            raw_prod = order.get("product")
            if isinstance(raw_prod, str):
                product_id = raw_prod

    if not product_id:
        raise WebhookGrantError("WEBHOOK_MALFORMED", "missing product id")

    try:
        pack = get_pack_by_creem_product_id(product_id, product_id_by_pack=product_id_by_pack)
    except LookupError as exc:
        raise WebhookGrantError("UNKNOWN_PRODUCT", str(exc)) from exc

    request_id = obj.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise WebhookGrantError("WEBHOOK_MALFORMED", "request_id must be string")

    metadata_raw = obj.get("metadata")
    if metadata_raw is None:
        metadata: dict[str, Any] = {}
    elif isinstance(metadata_raw, dict):
        metadata = metadata_raw
    else:
        raise WebhookGrantError("WEBHOOK_MALFORMED", "metadata must be object")

    return CheckoutCompletedGrant(
        event_id=event_id.strip(),
        creem_checkout_id=checkout_id.strip(),
        creem_order_id=order_id.strip() if order_id else None,
        creem_product_id=product_id.strip(),
        request_id=request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
        amount=amount,
        currency=currency,
        metadata=metadata,
        pack=pack,
    )
