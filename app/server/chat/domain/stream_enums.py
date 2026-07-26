from enum import StrEnum


class RecoveryReason(StrEnum):
    EMPTY_RESPONSE = "empty_response"
    TOOL_ERRORS_EXHAUSTED = "tool_errors_exhausted"
    NO_FINAL_ANSWER = "no_final_answer"


class RecoveryOutcome(StrEnum):
    NOT_USED = "not_used"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    MAX_STEPS = "max_steps"
    MAX_TOOLS = "max_tools"
    WALL_CLOCK = "wall_clock"
    ERROR = "error"


class TurnStreamPhase(StrEnum):
    MAIN = "main"
    RESUME = "resume"


class StreamErrorCode(StrEnum):
    AGENT_RECOVERY_EXHAUSTED = "agent_recovery_exhausted"
    CANVAS_DUPLICATE_TURN = "canvas_duplicate_turn"
    CANVAS_EPISODE_BUSY = "canvas_episode_busy"
    EMPTY_RESPONSE = "empty_response"
    EXECUTION_LOST = "execution_lost"
    GENERATION_FAILED = "generation_failed"
    GENERATION_TIMEOUT = "generation_timeout"
    GATEWAY_UPSTREAM_FAILED = "gateway_upstream_failed"
    INTERNAL = "internal"
    MAX_ITERATIONS = "max_iterations"
    TOOL_PARSE_FATAL = "tool_parse_fatal"
    TURN_FAILED = "turn_failed"
    WALL_CLOCK = "wall_clock"


class StreamFrameType(StrEnum):
    TOKEN = "token"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    DONE = "done"
    CANCELLED = "cancelled"
    CONVERSATION_TITLE = "conversation_title"
    CANVAS_PATCH = "canvas_patch"
    GENERATION_PROGRESS = "generation_progress"
    TOOL_PENDING = "tool_pending"
    USER_GATE_REQUIRED = "user_gate_required"
    BROWSER_BLOCKED = "browser_blocked"
    BROWSER_FRAME = "browser_frame"


class TokenChannel(StrEnum):
    ANSWER = "answer"
    THINK = "think"


_GATEWAY_STREAM_ERROR_ALIASES = frozenset(
    {
        "gateway_empty_stream",
        "gateway_upstream_timeout",
        "gateway_upstream_failed",
    }
)


def normalize_stream_error_code(
    value: str | None,
    *,
    fallback: StreamErrorCode = StreamErrorCode.INTERNAL,
) -> StreamErrorCode:
    if value is None:
        return fallback
    if value in _GATEWAY_STREAM_ERROR_ALIASES:
        return StreamErrorCode.GATEWAY_UPSTREAM_FAILED
    try:
        return StreamErrorCode(value)
    except ValueError:
        return fallback
