"""Collect one AIMessage from gateway streaming (no sync retry)."""

from __future__ import annotations

from typing import List

from langchain_core.messages import BaseMessage

from app.chat.llm.gateway_chat_model import GatewayChatModel
from app.chat.llm.thinking import build_ai_message


async def stream_to_ai_message(
    llm: GatewayChatModel,
    messages: List[BaseMessage],
    **kwargs,
) -> AIMessage:
    assembled_content = ""
    assembled_think = ""
    tool_calls: list = []
    async for chunk in llm._astream(messages, **kwargs):
        info = chunk.generation_info or {}
        if not info.get("assembled_step"):
            continue
        assembled_content = str(info.get("assembled_content") or "")
        assembled_think = str(info.get("assembled_think_content") or "")
        tool_calls = list(chunk.message.tool_calls or [])
        break
    return build_ai_message(
        content=assembled_content,
        tool_calls=tool_calls,
        reasoning=assembled_think or None,
    )
