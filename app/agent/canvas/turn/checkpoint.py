from __future__ import annotations

from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.memory.message_validate import has_unresolved_tool_calls, repair_unresolved_tool_calls
from app.server.infra.logger import logger


async def repair_canvas_checkpoint_if_needed(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    project_id: int,
    episode_id: int,
    turn_id: str,
    reason: str = "turn_interrupted",
) -> bool:
    """修复 checkpoint 中未完成的 tool_calls, 防止下一轮 LLM 请求被网关拒绝"""
    snap = await agent.aget_state(config)
    messages: list[BaseMessage] = list(snap.values.get("messages") or [])
    if not has_unresolved_tool_calls(messages):
        return False
    repaired = repair_unresolved_tool_calls(messages, reason=reason)
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]},
    )
    logger.warning(
        "canvas.checkpoint.repaired_unresolved_tool_calls",
        project_id=project_id,
        episode_id=episode_id,
        turn_id=turn_id,
        message_count=len(messages),
        reason=reason,
    )
    return True
