from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.agent.runtime.agent.events import InvalidToolCall
from app.agent.runtime.turn.enums import TurnTerminatedBy


class TurnEventKind(str, Enum):
    TURN_STARTING = "turn_starting"
    TURN_STARTED = "turn_started"
    MODEL_TOKEN = "model_token"
    MODEL_STEP_FINISHED = "model_step_finished"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TURN_INTERRUPTED = "turn_interrupted"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_ENDED = "turn_ended"


class TurnDispatch(str, Enum):
    BARRIER = "barrier"
    BROADCAST = "broadcast"


@dataclass(frozen=True)
class TurnStarting:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_STARTING
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BARRIER
    turn_id: str
    conversation_id: int | str
    user_id: int | str
    input_messages: list[BaseMessage]
    client_turn_id: str | None = None
    mode: str | None = None
    is_resume: bool = False


@dataclass(frozen=True)
class TurnStarted:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_STARTED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    thread_id: str
    config: RunnableConfig
    started_at: datetime


@dataclass(frozen=True)
class ModelToken:
    kind: ClassVar[TurnEventKind] = TurnEventKind.MODEL_TOKEN
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    step_index: int
    channel: Literal["answer", "think"] | str
    text: str


@dataclass(frozen=True)
class ModelStepFinished:
    kind: ClassVar[TurnEventKind] = TurnEventKind.MODEL_STEP_FINISHED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    step_index: int
    ai_message: AIMessage | None = None
    invalid_tool_calls: list[InvalidToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolStarted:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TOOL_STARTED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    step_index: int
    call_id: str
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    synthetic: bool = False
    recovery_attempt: int | None = None


@dataclass(frozen=True)
class ToolFinished:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TOOL_FINISHED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    step_index: int
    call_id: str
    tool_name: str
    tool_result: str = ""
    tool_error: bool = False
    error_class: str | None = None
    synthetic: bool = False
    recovery_attempt: int | None = None


@dataclass(frozen=True)
class TurnInterrupted:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_INTERRUPTED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BARRIER
    turn_id: str
    interrupts: list[Any]
    pending_tool_calls: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class TurnCompleted:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_COMPLETED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BARRIER
    turn_id: str
    messages: list[BaseMessage]
    answer_text: str
    tool_calls_count: int
    terminated_by: TurnTerminatedBy = TurnTerminatedBy.COMPLETED


@dataclass(frozen=True)
class TurnFailed:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_FAILED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BARRIER
    turn_id: str
    error: str
    error_class: str | None
    step_index: int | None = None
    messages: list[BaseMessage] | None = None
    terminated_by: TurnTerminatedBy = TurnTerminatedBy.TURN_FAILED


@dataclass(frozen=True)
class TurnEnded:
    kind: ClassVar[TurnEventKind] = TurnEventKind.TURN_ENDED
    dispatch: ClassVar[TurnDispatch] = TurnDispatch.BROADCAST
    turn_id: str
    terminated_by: TurnTerminatedBy
    failed_emitted: bool = False


TurnEvent = (
    TurnStarting
    | TurnStarted
    | ModelToken
    | ModelStepFinished
    | ToolStarted
    | ToolFinished
    | TurnInterrupted
    | TurnCompleted
    | TurnFailed
    | TurnEnded
)
