from __future__ import annotations

from langchain_core.messages import BaseMessage, ToolMessage

HISTORY_TOOL_MAX_CHARS = 8_000


def truncate_oversized_tool_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """对超大 ToolMessage 做物理截断并标注 [已截断]（token 上限，非语义裁剪）。"""
    out: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage) and len(str(message.content or "")) > HISTORY_TOOL_MAX_CHARS:
            preview = str(message.content)[:HISTORY_TOOL_MAX_CHARS]
            out.append(
                ToolMessage(
                    content=preview + "\n\n[已截断]",
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                )
            )
        else:
            out.append(message)
    return out
