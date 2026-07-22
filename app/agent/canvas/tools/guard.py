from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from langchain_core.tools import StructuredTool

from app.agent.chat.tools.result import INTERNAL, ToolResult
from app.server.infra.logger import logger

ToolCoroutine = Callable[..., Coroutine[Any, Any, str]]


def guard_canvas_tool(tool: StructuredTool) -> StructuredTool:
    """捕获工具未处理异常, 返回结构化 ToolResult, 避免炸掉 LangGraph tool loop"""
    coro = tool.coroutine
    if coro is None:
        return tool
    tool_name = tool.name or "unknown"

    async def guarded(*args: Any, **kwargs: Any) -> str:
        try:
            return await coro(*args, **kwargs)
        except Exception as exc:
            logger.exception(
                "canvas.tool.unhandled_error",
                tool_name=tool_name,
                error_type=INTERNAL,
            )
            return ToolResult.fail(INTERNAL, detail=str(exc)).to_tool_message()

    return tool.model_copy(update={"coroutine": guarded, "func": None})
