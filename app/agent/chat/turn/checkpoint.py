"""Chat turn checkpoint snapshot/restore on user cancel."""

from __future__ import annotations

from langchain_core.messages import BaseMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from app.agent.canvas.turn.checkpoint import repair_canvas_checkpoint_if_needed
from app.server.infra.logger import logger


async def capture_turn_checkpoint_messages(
    agent: CompiledStateGraph,
    config: RunnableConfig,
) -> list[BaseMessage]:
    snap = await agent.aget_state(config)
    return list(snap.values.get("messages") or [])


async def restore_turn_checkpoint(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    messages: list[BaseMessage],
    *,
    conversation_id: int,
    turn_id: str,
    reason: str = "user_cancel",
) -> None:
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]},
    )
    logger.info(
        "chat.checkpoint.restored",
        conversation_id=conversation_id,
        turn_id=turn_id,
        message_count=len(messages),
        reason=reason,
    )


async def repair_turn_checkpoint_on_cancel(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    conversation_id: int,
    turn_id: str,
    turn_start_messages: list[BaseMessage] | None,
    reason: str = "user_cancel",
) -> bool:
    if turn_start_messages is not None:
        await restore_turn_checkpoint(
            agent,
            config,
            turn_start_messages,
            conversation_id=conversation_id,
            turn_id=turn_id,
            reason=reason,
        )
        return True
    return await repair_canvas_checkpoint_if_needed(
        agent,
        config,
        project_id=conversation_id,
        turn_id=turn_id,
        reason=reason,
    )
