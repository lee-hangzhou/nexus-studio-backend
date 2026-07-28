"""跨 surface 的 tool_call / ToolMessage 序列卫生."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.runtime.tools.result import TURN_INTERRUPTED, ToolResult
from app.server.infra.logger import logger


def repair_unresolved_tool_calls(
    messages: list[BaseMessage],
    *,
    reason: str = TURN_INTERRUPTED,
    detail: str = "Tool call did not complete before the turn ended.",
    source: str = "checkpoint",
) -> list[BaseMessage]:
    """为未配对的 assistant tool_calls 补 synthetic ToolMessage, 避免下一轮请求网关报错"""
    out: list[BaseMessage] = []
    pending: list[tuple[str, str]] = []

    def flush_pending() -> None:
        """把当前 pending tool_calls 写成失败 ToolMessage"""
        nonlocal pending
        for call_id, name in pending:
            if not call_id:
                continue
            out.append(
                ToolMessage(
                    content=ToolResult.fail(reason, detail=detail).to_tool_message(),
                    tool_call_id=call_id,
                    name=name or "unknown",
                )
            )
            logger.warning(
                "chat.memory.unresolved_tool_repaired",
                tool_call_id=call_id,
                tool_name=name,
                error_type=reason,
                source=source,
            )
        pending = []

    for message in messages:
        if isinstance(message, AIMessage):
            if pending:
                flush_pending()
            out.append(message)
            pending = [
                (str(call.get("id") or ""), str(call.get("name") or ""))
                for call in (message.tool_calls or [])
                if call.get("id")
            ]
            continue

        if isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            if call_id and any(pid == call_id for pid, _ in pending):
                pending = [(pid, pname) for pid, pname in pending if pid != call_id]
                out.append(message)
            else:
                logger.warning(
                    "chat.memory.orphan_tool_dropped",
                    tool_call_id=call_id,
                    tool_name=message.name,
                )
            continue

        if pending:
            flush_pending()
        out.append(message)

    if pending:
        flush_pending()
    return out


def has_unresolved_tool_calls(messages: list[BaseMessage]) -> bool:
    """是否存在 assistant tool_calls 尚未有对应 ToolMessage"""
    pending: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            pending = {
                str(call.get("id") or "")
                for call in (message.tool_calls or [])
                if call.get("id")
            }
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            if call_id in pending:
                pending.discard(call_id)
        elif isinstance(message, HumanMessage) and pending:
            return True
        else:
            pending = set()
    return bool(pending)
