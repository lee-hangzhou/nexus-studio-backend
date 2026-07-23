"""Gateway / AppError → TurnTerminatedBy mapping for runner and SSE projection."""

from __future__ import annotations

from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def terminated_by_for_app_error(exc: AppError) -> TurnTerminatedBy:
    """Map product AppError to a stable turn termination reason."""
    if exc.code == int(ErrorCode.SERVICE_UNAVAILABLE):
        return TurnTerminatedBy.GATEWAY_UPSTREAM_TIMEOUT
    if exc.code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR):
        return TurnTerminatedBy.GATEWAY_EMPTY_STREAM
    return TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED


def stream_error_class_for_app_error(exc: AppError) -> str:
    """Legacy string form used by AgentEvent / SSE normalize (value == enum)."""
    return terminated_by_for_app_error(exc).value


def terminated_by_for_error_class(error_class: str | None) -> TurnTerminatedBy:
    """Resolve a stream/tool error_class string to TurnTerminatedBy once."""
    if error_class is None:
        return TurnTerminatedBy.TURN_FAILED
    try:
        return TurnTerminatedBy(error_class)
    except ValueError:
        return TurnTerminatedBy.TURN_FAILED
