"""Structured errors from the model gateway boundary."""

from __future__ import annotations


class GatewayChatError(Exception):
    """Raised when the gateway returns no usable model output."""

    def __init__(
        self,
        error_type: str,
        detail: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.error_type = error_type
        self.detail = detail
        self.retryable = retryable
        self.status_code = status_code


def gateway_error_from_http_status(status_code: int, body: bytes | str) -> GatewayChatError:
    preview = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    preview = preview.strip()[:500]
    if status_code in {502, 503, 504, 524}:
        return GatewayChatError(
            "gateway_upstream_timeout",
            preview or f"gateway upstream HTTP {status_code}",
            status_code=status_code,
        )
    return GatewayChatError(
        "gateway_upstream_failed",
        preview or f"gateway upstream HTTP {status_code}",
        retryable=False,
        status_code=status_code,
    )
