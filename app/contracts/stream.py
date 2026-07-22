from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.chat.domain.stream_enums import StreamErrorCode, StreamFrameType, TokenChannel


class StreamContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    protocol_version: int = Field(default=2, ge=1)


class ToolRecoveryFrameData(StreamContract):
    protocol_version: int = Field(default=2, exclude=True)
    synthetic: bool = False
    recoverable: bool = False
    recovery_attempt: int | None = Field(default=None, ge=1)
    error_code: str | None = None


class TokenFrame(StreamContract):
    type: Literal[StreamFrameType.TOKEN]
    channel: TokenChannel
    text: str


class ToolStartFrame(StreamContract):
    type: Literal[StreamFrameType.TOOL_START]
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolEndFrame(StreamContract):
    type: Literal[StreamFrameType.TOOL_END]
    call_id: str
    name: str
    ok: bool
    preview: str = ""
    data: ToolRecoveryFrameData | None = None


class HeartbeatFrame(StreamContract):
    type: Literal[StreamFrameType.HEARTBEAT]
    ts: int
    turn_id: str | None = None


class ErrorFrame(StreamContract):
    type: Literal[StreamFrameType.ERROR]
    code: StreamErrorCode
    message: str
    turn_id: str | None = None
    data: dict[str, Any] | None = None


class DoneFrame(StreamContract):
    type: Literal[StreamFrameType.DONE]
    turn_id: str
    message_ids: list[int] = Field(default_factory=list)


class CancelledFrame(StreamContract):
    type: Literal[StreamFrameType.CANCELLED]
    turn_id: str
    reason: str


class ConversationTitleFrame(StreamContract):
    type: Literal[StreamFrameType.CONVERSATION_TITLE]
    conversation_id: int
    title: str
    updated_at: str


class CanvasPatchFrame(StreamContract):
    type: Literal[StreamFrameType.CANVAS_PATCH]
    data: CanvasPatchResponse
    turn_id: str | None = None


class GenerationProgressFrame(StreamContract):
    type: Literal[StreamFrameType.GENERATION_PROGRESS]
    data: GenerationProgress
    turn_id: str | None = None


class ToolPendingFrame(StreamContract):
    type: Literal[StreamFrameType.TOOL_PENDING]
    call_id: str
    name: str
    summary: str | None = None
    turn_id: str


class UserGateRequiredFrame(StreamContract):
    type: Literal[StreamFrameType.USER_GATE_REQUIRED]
    turn_id: str
    gate_id: str
    gate_type: str
    prompt: str = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)
    assets: dict[str, Any] = Field(default_factory=dict)
    choices: list[dict[str, Any]] = Field(default_factory=list)
    phase: str | None = None
    domain: str | None = None


class BrowserBlockedFrame(StreamContract):
    type: Literal[StreamFrameType.BROWSER_BLOCKED]
    turn_id: str
    message: str
    screenshot_url: str | None = None
    conversation_id: int


class BrowserFrameEvent(StreamContract):
    type: Literal[StreamFrameType.BROWSER_FRAME]
    frame_b64: str
    width: int
    height: int


StreamFrame = Annotated[
    TokenFrame
    | ToolStartFrame
    | ToolEndFrame
    | HeartbeatFrame
    | ErrorFrame
    | DoneFrame
    | CancelledFrame
    | ConversationTitleFrame
    | CanvasPatchFrame
    | GenerationProgressFrame
    | ToolPendingFrame
    | UserGateRequiredFrame
    | BrowserBlockedFrame
    | BrowserFrameEvent,
    Field(discriminator="type"),
]

STREAM_FRAME_ADAPTER: TypeAdapter[StreamFrame] = TypeAdapter(StreamFrame)


def create_stream_frame(**payload: Any) -> StreamFrame:
    frame: StreamFrame = STREAM_FRAME_ADAPTER.validate_python(payload)
    return frame
