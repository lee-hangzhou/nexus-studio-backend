"""Turn start/cleanup 的 checkpoint repair 调用与共享写回."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

from app.agent.runtime.turn.message_hygiene import (
    has_unresolved_tool_calls,
    repair_unresolved_tool_calls,
)
from app.server.infra.logger import logger


class CheckpointRepairFn(Protocol):
    """Surface 注入的 checkpoint repair 回调协议"""

    async def __call__(
        self,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
        /,
        **kwargs: Any,
    ) -> None:
        """执行 surface 侧 checkpoint 修复"""
        ...


async def repair_unresolved_checkpoint_if_needed(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    reason: str,
) -> int | None:
    """若存在未完成 tool_calls 则写回修复后的 checkpoint, 返回修复前消息数"""
    snap = await agent.aget_state(config)
    messages: list[BaseMessage] = list(snap.values.get("messages") or [])
    if not has_unresolved_tool_calls(messages):
        return None
    repaired = repair_unresolved_tool_calls(messages, reason=reason)
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]},
    )
    return len(messages)


async def run_checkpoint_repair(
    repair: CheckpointRepairFn | None,
    *,
    agent: CompiledStateGraph,
    runnable_config: RunnableConfig,
    thread_id: str,
    turn_id: str,
    reason: str,
    **extra: Any,
) -> None:
    """按稳定 kwargs 调用 surface repair, 失败只记日志不抛出"""
    if repair is None:
        return
    try:
        await repair(
            agent,
            runnable_config,
            thread_id=thread_id,
            turn_id=turn_id,
            reason=reason,
            **extra,
        )
    except Exception as exc:
        logger.exception(
            "turn.checkpoint_repair_failed",
            turn_id=turn_id,
            reason=reason,
            error=str(exc),
        )
