from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.agent.runtime.turn.message_hygiene import (  # noqa: F401
    has_unresolved_tool_calls,
    repair_unresolved_tool_calls,
)
from app.server.infra.logger import logger

__all__ = [
    "has_unresolved_tool_calls",
    "repair_unresolved_tool_calls",
    "sanitize_tool_pairs",
    "validate_langchain_tool_sequence",
]


def sanitize_tool_pairs(messages: list[BaseMessage]) -> list[BaseMessage]:
    """丢弃没有对应 assistant tool_calls 的孤儿 ToolMessage"""
    out: list[BaseMessage] = []
    pending_ids: set[str] = set()

    for message in messages:
        if isinstance(message, AIMessage):
            out.append(message)
            pending_ids = {
                str(call.get("id") or "")
                for call in (message.tool_calls or [])
                if call.get("id")
            }
            continue

        if isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            if call_id and call_id in pending_ids:
                out.append(message)
                pending_ids.discard(call_id)
            else:
                logger.warning(
                    "chat.memory.orphan_tool_dropped",
                    tool_call_id=call_id,
                    tool_name=message.name,
                )
            continue

        out.append(message)
        pending_ids = set()

    return out


def validate_langchain_tool_sequence(messages: list[BaseMessage]) -> None:
    """记录无法匹配到先前 assistant tool_calls 的 ToolMessage"""
    pending_ids: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            pending_ids = {
                str(call.get("id") or "")
                for call in (message.tool_calls or [])
                if call.get("id")
            }
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            if not call_id or call_id not in pending_ids:
                logger.error(
                    "chat.openai.invalid_tool_sequence",
                    tool_call_id=call_id,
                    tool_name=message.name,
                )
        else:
            pending_ids = set()
