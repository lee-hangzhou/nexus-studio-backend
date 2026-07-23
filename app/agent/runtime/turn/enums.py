"""Turn harness mechanism enums (termination + guard disposition)."""

from __future__ import annotations

from enum import StrEnum


class TurnTerminatedBy(StrEnum):
    """Why a turn ended. Values project to SSE StreamErrorCode / done semantics."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    WALL_CLOCK = "wall_clock"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOOLS = "max_tools"
    GUARD_STOP = "guard_stop"
    GUARD_ERROR = "error"
    TURN_FAILED = "turn_failed"
    TOOL_PARSE_FATAL = "tool_parse_fatal"
    INTERNAL = "internal"
    GATEWAY_UPSTREAM_TIMEOUT = "gateway_upstream_timeout"
    GATEWAY_EMPTY_STREAM = "gateway_empty_stream"
    GATEWAY_UPSTREAM_FAILED = "gateway_upstream_failed"
    GATEWAY_PROTOCOL_ERROR = "gateway_protocol_error"
    AGENT_RECOVERY_EXHAUSTED = "agent_recovery_exhausted"
    EMPTY_RESPONSE = "empty_response"


class GuardAction(StrEnum):
    """Disposition after a tool finishes."""

    CONTINUE = "continue"
    STOP_TURN = "stop_turn"
    BROWSER_BLOCKED = "browser_blocked"
