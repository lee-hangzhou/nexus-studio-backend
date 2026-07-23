"""Structured interrupt classification: tool approval vs user gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UserGateInterrupt(BaseModel):
    """Chat Human-in-the-loop gate payload (not a tool-approval pending)."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["user_gate"] = "user_gate"
    gate_type: str
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


InterruptKind = UserGateInterrupt | PendingToolAction


def classify_interrupt_value(
    value: Any,
    *,
    pending_tool_calls: list[dict[str, Any]] | None = None,
    action_index_base: int = 0,
) -> list[InterruptKind]:
    """Classify one LangGraph interrupt value into typed HITL kinds."""
    if not isinstance(value, dict):
        return []
    if "gate_type" in value:
        return [
            UserGateInterrupt(
                gate_type=str(value.get("gate_type") or ""),
                gate_id=str(value["gate_id"]) if value.get("gate_id") is not None else None,
                prompt=str(value["prompt"]) if value.get("prompt") is not None else None,
            )
        ]

    pending_by_call_id: dict[str, dict[str, Any]] = {}
    for item in pending_tool_calls or []:
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            pending_by_call_id[call_id] = item

    out: list[InterruptKind] = []
    action_requests = value.get("action_requests")
    if isinstance(action_requests, list):
        for offset, item in enumerate(action_requests):
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id") or item.get("call_id")
            call_id = raw_id if isinstance(raw_id, str) and raw_id else ""
            name = str(item.get("name") or "")
            summary = str(item.get("description") or item.get("summary") or "")
            args = item.get("args") if isinstance(item.get("args"), dict) else None
            checkpoint = pending_by_call_id.get(call_id) if call_id else None
            if checkpoint is not None:
                name = str(checkpoint.get("name") or name)
                cargs = checkpoint.get("args")
                if args is None and isinstance(cargs, dict):
                    args = cargs
                if not summary and isinstance(cargs, dict):
                    summary = str(cargs)
            if not call_id or not name:
                continue
            out.append(
                PendingToolAction(
                    call_id=call_id,
                    name=name,
                    summary=summary,
                    args=args,
                    approval_index=action_index_base + offset,
                )
            )
        return out

    call_id = str(value.get("call_id") or "")
    name = str(value.get("name") or "")
    summary = str(value.get("summary") or value.get("description") or "")
    if not call_id or not name:
        return []
    args = value.get("args") if isinstance(value.get("args"), dict) else None
    out.append(
        PendingToolAction(
            call_id=call_id,
            name=name,
            summary=summary,
            args=args,
            approval_index=action_index_base,
        )
    )
    return out


def parse_pending_tool_actions(
    interrupts: list[Any],
    *,
    pending_tool_calls: list[Any] | None = None,
) -> list[PendingToolAction]:
    """Only tool-approval actions (UserGate excluded)."""
    pending_dicts = [item for item in (pending_tool_calls or []) if isinstance(item, dict)]
    actions: list[PendingToolAction] = []
    index_base = 0
    for interrupt in interrupts:
        classified = classify_interrupt_value(
            interrupt,
            pending_tool_calls=pending_dicts,
            action_index_base=index_base,
        )
        for item in classified:
            if isinstance(item, PendingToolAction):
                actions.append(item)
                index_base += 1
    return actions
