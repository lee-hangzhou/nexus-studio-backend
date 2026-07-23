"""Structured errors from the model gateway chat protocol boundary.

Transport / HTTP failures from GatewayClient raise AppError.
GatewayChatError remains for OpenAI-compatible stream/response assembly.
"""

from __future__ import annotations


class GatewayChatError(Exception):
    """Raised when chat protocol parsing yields no usable model output."""

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
