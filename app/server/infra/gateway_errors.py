"""Structured errors from the model gateway chat protocol boundary.

Transport / HTTP failures from GatewayClient raise AppError.
GatewayChatError remains for OpenAI-compatible stream/response assembly.
"""

from __future__ import annotations

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


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


def stream_error_class_for_app_error(exc: AppError) -> str:
    """Map product AppError to legacy stream error_class recognized by SSE/orchestrator."""
    if exc.code == int(ErrorCode.SERVICE_UNAVAILABLE):
        return "gateway_upstream_timeout"
    if exc.code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR):
        return "gateway_empty_stream"
    return "gateway_upstream_failed"
