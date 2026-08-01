from __future__ import annotations

from app.server.billing.domain.webhook_signature import (
    sign_creem_webhook,
    verify_creem_webhook_signature,
)


def test_valid_signature_is_accepted() -> None:
    body = b'{"id":"evt_1","eventType":"checkout.completed"}'
    secret = "whsec_test"
    signature = sign_creem_webhook(body, secret)
    assert verify_creem_webhook_signature(body, signature, secret) is True


def test_tampered_body_is_rejected() -> None:
    secret = "whsec_test"
    signature = sign_creem_webhook(b'{"id":"evt_1"}', secret)
    assert verify_creem_webhook_signature(b'{"id":"evt_2"}', signature, secret) is False


def test_missing_signature_is_rejected() -> None:
    assert verify_creem_webhook_signature(b"{}", None, "whsec_test") is False
    assert verify_creem_webhook_signature(b"{}", "", "whsec_test") is False
