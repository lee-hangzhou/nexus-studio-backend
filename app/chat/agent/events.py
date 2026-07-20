from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage


@dataclass(frozen=True)
class InvalidToolCall:
    call_id: str
    name: str
    raw_arguments: str
    parse_error: str


class AgentEventType(str, Enum):
    MODEL_TOKEN = "model_token"
    MODEL_STEP_FINISHED = "model_step_finished"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    turn_id: str
    step_index: int
    channel: Literal["answer", "think"] | None = None
    text: str | None = None
    ai_message: AIMessage | None = None
    invalid_tool_calls: list[InvalidToolCall] = field(default_factory=list)
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    tool_error: bool = False
    synthetic: bool = False
    recovery_attempt: int | None = None
    error_class: str | None = None
    messages: list[BaseMessage] | None = None
    error: str | None = None
