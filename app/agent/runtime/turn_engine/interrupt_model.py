"""Structured interrupt classification: tool approval vs user gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class UserGateInterrupt(BaseModel):
    """Chat Human-in-the-loop gate payload (not a tool-approval pending)."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["user_gate"] = "user_gate"
    gate_type: str = Field(min_length=1)
    gate_id: str | None = None
    prompt: str | None = None


class PendingToolAction(BaseModel):
    """Canvas / HITL tool approval pending action."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_approval"] = "tool_approval"
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str = ""
    args: dict[str, Any] | None = None
    approval_index: int | None = None


class _PendingToolCallCheckpoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    call_id: str = Field(min_length=1)
    name: str | None = None
    args: dict[str, Any] | None = None


class _ActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    call_id: str | None = None
    name: str | None = None
    description: str | None = None
    summary: str | None = None
    args: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_action_id(self) -> _ActionRequest:
        raw_id = self.id if self.id is not None else self.call_id
        if not isinstance(raw_id, str) or not raw_id:
            raise ValueError("action_request.id must be a non-empty string")
        return self

    @property
    def resolved_call_id(self) -> str:
        raw_id = self.id if self.id is not None else self.call_id
        assert isinstance(raw_id, str) and raw_id
        return raw_id


class _InterruptValue(BaseModel):
    model_config = ConfigDict(extra="allow")

    gate_type: str | None = None
    gate_id: Any = None
    prompt: Any = None
    action_requests: list[_ActionRequest] | None = None
    call_id: str | None = None
    name: str | None = None
    summary: Any = None
    description: Any = None
    args: dict[str, Any] | None = None


_PENDING_TOOL_CALLS = TypeAdapter(list[_PendingToolCallCheckpoint])

InterruptKind = UserGateInterrupt | PendingToolAction


def classify_interrupt_value(
    value: Any,
    *,
    pending_tool_calls: list[_PendingToolCallCheckpoint] | None = None,
    action_index_base: int = 0,
) -> list[InterruptKind]:
    """Classify one LangGraph interrupt value into typed HITL kinds."""
    payload = _InterruptValue.model_validate(value)

    if payload.gate_type is not None:
        return [UserGateInterrupt.model_validate(value)]

    pending_by_call_id = {
        row.call_id: row for row in (pending_tool_calls or [])
    }

    if payload.action_requests is not None:
        out: list[InterruptKind] = []
        for offset, row in enumerate(payload.action_requests):
            call_id = row.resolved_call_id
            name = row.name or ""
            summary_text = ""
            if row.description is not None:
                summary_text = str(row.description)
            elif row.summary is not None:
                summary_text = str(row.summary)
            args = row.args
            checkpoint = pending_by_call_id.get(call_id)
            if checkpoint is not None:
                if checkpoint.name:
                    name = checkpoint.name
                if args is None:
                    args = checkpoint.args
                if not summary_text and args is not None:
                    summary_text = str(args)
            out.append(
                PendingToolAction(
                    call_id=call_id,
                    name=name,
                    summary=summary_text,
                    args=args,
                    approval_index=action_index_base + offset,
                )
            )
        return out

    return [
        PendingToolAction(
            call_id=payload.call_id or "",
            name=payload.name or "",
            summary=(
                str(payload.summary)
                if payload.summary is not None
                else (str(payload.description) if payload.description is not None else "")
            ),
            args=payload.args,
            approval_index=action_index_base,
        )
    ]


def parse_pending_tool_actions(
    interrupts: list[Any],
    *,
    pending_tool_calls: list[Any] | None = None,
) -> list[PendingToolAction]:
    """Only tool-approval actions (UserGate excluded)."""
    pending = _PENDING_TOOL_CALLS.validate_python(pending_tool_calls or [])
    actions: list[PendingToolAction] = []
    index_base = 0
    for interrupt in interrupts:
        classified = classify_interrupt_value(
            interrupt,
            pending_tool_calls=pending,
            action_index_base=index_base,
        )
        for item in classified:
            if isinstance(item, PendingToolAction):
                actions.append(item)
                index_base += 1
    return actions
