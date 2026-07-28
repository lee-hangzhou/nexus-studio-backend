from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.runtime.turn_engine.checkpoint import repair_unresolved_checkpoint_if_needed
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
    """Canvas surface: 走共享 unresolved checkpoint 修复并打集维度日志"""
    message_count = await repair_unresolved_checkpoint_if_needed(
        agent,
        config,
        reason=reason,
    )
    if message_count is None:
        return False
    logger.warning(
        "canvas.checkpoint.repaired_unresolved_tool_calls",
        project_id=project_id,
        episode_id=episode_id,
        turn_id=turn_id,
        message_count=message_count,
        reason=reason,
    )
    return True
