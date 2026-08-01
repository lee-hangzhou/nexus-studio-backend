from __future__ import annotations

import pytest

from app.server.billing.domain.grant import (
    WebhookGrantError,
    parse_checkout_completed_grant,
)
from app.server.billing.domain.packs import CreditPackKey


PRODUCT_MAP = {
    CreditPackKey.USD_5: "prod_5",
    CreditPackKey.USD_15: "prod_15",
    CreditPackKey.USD_50: "prod_50",
    CreditPackKey.USD_100: "prod_100",
}


def _completed_payload(**overrides: object) -> dict:
    payload: dict = {
        "id": "evt_1",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_1",
            "request_id": "ord_local_1",
            "product": {"id": "prod_15"},
            "order": {
                "id": "ord_creem_1",
                "amount": 1500,
                "currency": "USD",
                "status": "paid",
            },
            "metadata": {"user_id": "42"},
            "status": "completed",
        },
    }
    payload.update(overrides)
    return payload


def test_checkout_completed_maps_to_credit_pack() -> None:
    grant = parse_checkout_completed_grant(_completed_payload(), product_id_by_pack=PRODUCT_MAP)
    assert grant is not None
    assert grant.event_id == "evt_1"
    assert grant.creem_checkout_id == "ch_1"
    assert grant.pack.key == CreditPackKey.USD_15
    assert grant.pack.credits == 150
    assert grant.metadata["user_id"] == "42"


def test_non_checkout_event_is_ignored() -> None:
    grant = parse_checkout_completed_grant(
        {"id": "evt_2", "eventType": "subscription.paid", "object": {}},
        product_id_by_pack=PRODUCT_MAP,
    )
    assert grant is None


def test_unknown_product_fail_closed() -> None:
    with pytest.raises(WebhookGrantError) as exc:
        parse_checkout_completed_grant(
            _completed_payload(object={
                "id": "ch_1",
                "product": {"id": "prod_other"},
                "order": {"id": "ord_1", "amount": 1, "currency": "USD"},
                "metadata": {},
            }),
            product_id_by_pack=PRODUCT_MAP,
        )
    assert exc.value.code == "UNKNOWN_PRODUCT"


def test_missing_event_type_fail_closed() -> None:
    with pytest.raises(WebhookGrantError) as exc:
        parse_checkout_completed_grant({"id": "evt_1"}, product_id_by_pack=PRODUCT_MAP)
    assert exc.value.code == "WEBHOOK_MALFORMED"


def test_missing_metadata_defaults_to_empty_object() -> None:
    payload = _completed_payload()
    del payload["object"]["metadata"]
    grant = parse_checkout_completed_grant(payload, product_id_by_pack=PRODUCT_MAP)
    assert grant is not None
    assert grant.metadata == {}


def test_non_object_metadata_fail_closed() -> None:
    payload = _completed_payload()
    payload["object"]["metadata"] = "nope"
    with pytest.raises(WebhookGrantError) as exc:
        parse_checkout_completed_grant(payload, product_id_by_pack=PRODUCT_MAP)
    assert exc.value.code == "WEBHOOK_MALFORMED"
