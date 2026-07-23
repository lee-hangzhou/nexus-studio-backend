from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Literal

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
class ModelTokenEvent:
    type: ClassVar[AgentEventType] = AgentEventType.MODEL_TOKEN
    turn_id: str
    step_index: int
    channel: Literal["answer", "think"]
    text: str


@dataclass(frozen=True)
class ModelStepFinishedEvent:
    type: ClassVar[AgentEventType] = AgentEventType.MODEL_STEP_FINISHED
    turn_id: str
    step_index: int
    ai_message: AIMessage
    invalid_tool_calls: list[InvalidToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolStartedEvent:
    type: ClassVar[AgentEventType] = AgentEventType.TOOL_STARTED
    turn_id: str
    step_index: int
    call_id: str
    tool_name: str
    tool_args: dict[str, Any]


@dataclass(frozen=True)
class ToolFinishedEvent:
    type: ClassVar[AgentEventType] = AgentEventType.TOOL_FINISHED
    turn_id: str
    step_index: int
    call_id: str
    tool_name: str
    tool_args: dict[str, Any]
    tool_result: str
    tool_error: bool
    error_class: str | None

    def __post_init__(self) -> None:
        if self.tool_error and not self.error_class:
            raise ValueError("failed tool event requires error_class")
        if not self.tool_error and self.error_class is not None:
            raise ValueError("successful tool event cannot contain error_class")


@dataclass(frozen=True)
class TurnCompletedEvent:
    type: ClassVar[AgentEventType] = AgentEventType.TURN_COMPLETED
    turn_id: str
    step_index: int
    messages: list[BaseMessage]


@dataclass(frozen=True)
class TurnFailedEvent:
    type: ClassVar[AgentEventType] = AgentEventType.TURN_FAILED
    turn_id: str
    step_index: int
    error: str
    error_class: str


AgentEvent = (
    ModelTokenEvent
    | ModelStepFinishedEvent
    | ToolStartedEvent
    | ToolFinishedEvent
    | TurnCompletedEvent
    | TurnFailedEvent
)
