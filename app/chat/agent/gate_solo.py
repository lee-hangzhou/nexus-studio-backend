"""Gate solo batch guard — shared violation check and ToolMessage synthesis."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.chat.tools.result import GATE_BATCH_ISOLATION, ToolResult

GATE_SOLO_TOOL = "request_user_gate"
_BATCH_ISOLATION_DETAIL = "request_user_gate must be the only tool_call in this model step"


def batch_violation(tool_calls: list[dict[str, Any]]) -> bool:
    if not tool_calls:
        return False
    names = [str(c.get("name") or "") for c in tool_calls]
    if GATE_SOLO_TOOL not in names:
        return False
    if len(tool_calls) != 1:
        return True
    return names[0] != GATE_SOLO_TOOL


def last_ai_message_with_tool_calls(messages: list) -> tuple[int, AIMessage] | None:
    """Return the latest AIMessage only when it carries tool_calls for this step."""
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                return idx, msg
            return None
    return None


def synthesize_batch_error_tool_messages(tool_calls: list[dict[str, Any]]) -> list[ToolMessage]:
    content = ToolResult.fail(GATE_BATCH_ISOLATION, detail=_BATCH_ISOLATION_DETAIL).to_tool_message()
    out: list[ToolMessage] = []
    for call in tool_calls:
        call_id = str(call.get("id") or "")
        if not call_id:
            continue
        out.append(
            ToolMessage(
                content=content,
                tool_call_id=call_id,
                name=str(call.get("name") or ""),
                status="error",
            )
        )
    return out
