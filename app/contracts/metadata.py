from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.server.chat.domain.stream_enums import RecoveryOutcome, TerminationReason, TurnStreamPhase


class MetadataContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class TurnContextMetadata(MetadataContract):
    has_attachments: bool
    enable_tools: bool


class InvalidToolCallMetadata(MetadataContract):
    call_id: str
    name: str
    parse_error: str
    raw_length: int = Field(ge=0)


class ToolStepMetadata(MetadataContract):
    call_id: str
    name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    error_type: str | None = None
    result_preview: str = ""
    synthetic: bool = False


class ToolAuditMetadata(MetadataContract):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str
    latency_ms: float = Field(ge=0)


class ToolRecoveryMetadata(MetadataContract):
    recovered: bool = False
    recovery_type: str | None = None
    recovery_reason: str | None = None
    recovery_attempts: int = Field(default=0, ge=0)
    recovery_failure_reason: str | None = None
    recovery_exhausted: bool = False


class ArtifactMetadata(MetadataContract):
    attachment_id: int
    filename: str
    mime_type: str
    role: str
    turn_id: str


class TurnStreamObservationMetadata(MetadataContract):
    phase: TurnStreamPhase
    gate_id: str | None = None
    resume_action: str | None = None


class GateSuspendMetadata(MetadataContract):
    gate_id: str
    gate_type: str


class ToolRequestMetadata(MetadataContract):
    turn_context: TurnContextMetadata
    invalid_tool_calls: list[InvalidToolCallMetadata] = Field(default_factory=list)
    stream: TurnStreamObservationMetadata | None = None


class ToolResultMetadata(MetadataContract):
    name: str | None = None
    call_id: str
    error_class: str | None = None
    synthetic: bool = False
    recovery_reason: str | None = None
    recovery_attempt: int | None = Field(default=None, ge=1)
    stream: TurnStreamObservationMetadata | None = None
    gate_suspend: GateSuspendMetadata | None = None


class AssistantMessageMetadata(MetadataContract):
    turn_context: TurnContextMetadata
    invalid_tool_calls: list[InvalidToolCallMetadata] = Field(default_factory=list)
    tool_steps: list[ToolStepMetadata] = Field(default_factory=list)
    tool_audit: list[ToolAuditMetadata] = Field(default_factory=list)
    recovery: ToolRecoveryMetadata = Field(default_factory=ToolRecoveryMetadata)
    artifacts: list[ArtifactMetadata] = Field(default_factory=list)


class UserMessageMetadata(MetadataContract):
    turn_id: str
    client_turn_id: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)


class CanvasToolStepMetadata(MetadataContract):
    call_id: str | None = None
    name: str | None = None
    ok: bool
    preview: str = ""
    error_type: str | None = None


class CanvasMessageMetadata(MetadataContract):
    turn_id: str
    client_turn_id: str | None = None
    phase: str | None = None
    tool_calls_count: int | None = Field(default=None, ge=0)
    tool_step: CanvasToolStepMetadata | None = None


class TurnUsageRecord(MetadataContract):
    conversation_id: int
    turn_id: str
    model_key: str
    stream_phase: TurnStreamPhase = TurnStreamPhase.MAIN
    gate_id: str | None = None
    resume_action: str | None = None
    model_steps_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    wall_clock_seconds: float = Field(ge=0)
    terminated_by: TerminationReason
    termination_message: str | None = None
    limits: dict[str, int]
    attachments: list[str]
    model_steps: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    first_error: dict[str, Any] | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    exception_stack: str | None = None
    tool_recovery_count: int = Field(ge=0)
    empty_recovery_attempts: int = Field(ge=0)
    recovery_outcome: RecoveryOutcome
    recovery_failure_reason: str | None = None
    timestamp: datetime
