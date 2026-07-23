"""Map TurnTerminatedBy (engine) ↔ TerminationReason (usage / product)."""

from __future__ import annotations

from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.server.chat.domain.stream_enums import TerminationReason, normalize_stream_error_code
from app.server.chat.domain.stream_enums import StreamErrorCode


def termination_reason_for(terminated_by: TurnTerminatedBy | str) -> TerminationReason:
    """Collapse engine termination into the usage vocabulary."""
    value = terminated_by.value if isinstance(terminated_by, TurnTerminatedBy) else str(terminated_by)
    if value == TurnTerminatedBy.COMPLETED.value:
        return TerminationReason.COMPLETED
    if value == TurnTerminatedBy.INTERRUPTED.value:
        return TerminationReason.INTERRUPTED
    if value in {
        TurnTerminatedBy.MAX_ITERATIONS.value,
        "max_steps",
    }:
        return TerminationReason.MAX_STEPS
    if value == TurnTerminatedBy.MAX_TOOLS.value:
        return TerminationReason.MAX_TOOLS
    if value == TurnTerminatedBy.WALL_CLOCK.value:
        return TerminationReason.WALL_CLOCK
    return TerminationReason.ERROR


def stream_error_code_for(terminated_by: TurnTerminatedBy | str) -> StreamErrorCode:
    value = terminated_by.value if isinstance(terminated_by, TurnTerminatedBy) else str(terminated_by)
    return normalize_stream_error_code(value, fallback=StreamErrorCode.TURN_FAILED)
