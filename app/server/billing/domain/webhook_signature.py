from __future__ import annotations

import hashlib
import hmac


def sign_creem_webhook(raw_body: bytes, secret: str) -> str:
    """计算 Creem webhook HMAC-SHA256 签名"""
    if not secret:
        raise ValueError("webhook secret is empty")
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_creem_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """校验 Creem webhook 签名头"""
    if not signature_header or not secret:
        return False
    expected = sign_creem_webhook(raw_body, secret)
    return hmac.compare_digest(expected, signature_header.strip())
