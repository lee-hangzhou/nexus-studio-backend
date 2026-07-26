from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.agent.runtime.tools.result import MEMORY_UNAVAILABLE, ToolResult
from app.server.infra.config import settings
from app.server.infra.embeddings import GatewayEmbeddingError
from app.server.infra.logger import logger

_T = TypeVar("_T")


def is_memory_unavailable_error(exc: BaseException) -> bool:
    """判断是否为可降级的记忆可用性错误"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(exc, GatewayEmbeddingError):
        return True
    if isinstance(exc, RuntimeError) and "store" in str(exc).lower():
        return True
    return False


async def run_memory_tool_call(
    coro_factory: Callable[[], Awaitable[_T]],
    *,
    tool_name: str,
    timeout_sec: float | None = None,
) -> _T:
    """记忆工具统一超时，避免嵌入/检索挂死拖住整轮 turn"""
    timeout = timeout_sec if timeout_sec is not None else settings.MEMORY_TOOL_TIMEOUT_SEC
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"{tool_name} timed out after {timeout}s") from exc


def memory_tool_fail(
    *,
    tool_name: str,
    exc: BaseException,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> ToolResult:
    """将可用性错误转为统一 ToolResult"""
    logger.warning(
        "chat.memory.embedding_failed",
        tool_name=tool_name,
        user_id=user_id,
        conversation_id=conversation_id,
        error=str(exc),
        error_type=MEMORY_UNAVAILABLE,
    )
    return ToolResult.fail(MEMORY_UNAVAILABLE, detail=str(exc))


def format_memory_success(output: Any) -> ToolResult:
    """将工具原始输出包装为成功 ToolResult"""
    if isinstance(output, tuple):
        output = output[0]
    text = output if isinstance(output, str) else str(output)
    return ToolResult.ok(text)
