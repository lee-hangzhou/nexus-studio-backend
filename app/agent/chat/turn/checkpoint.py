from __future__ import annotations

from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.turn.gate_emit import has_pending_user_gate
from app.agent.chat.turn.upgrade_invite_emit import has_pending_upgrade_invite
from app.agent.runtime.turn_engine.checkpoint import repair_unresolved_checkpoint_if_needed
from app.server.infra.logger import logger


async def capture_turn_checkpoint_messages(
    agent: CompiledStateGraph,
    config: RunnableConfig,
) -> list[BaseMessage]:
    """读取 turn 开始前的 checkpoint messages 快照"""
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
    """用快照消息整体覆盖当前 checkpoint"""
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


async def repair_chat_checkpoint_if_needed(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    conversation_id: int,
    turn_id: str,
    reason: str = "stale_unresolved",
) -> bool:
    """有待处理 UserGate / 升级邀请 interrupt 时跳过; 否则走共享 unresolved checkpoint 修复"""
    if await has_pending_user_gate(agent, config):
        return False
    if await has_pending_upgrade_invite(agent, config):
        return False
    message_count = await repair_unresolved_checkpoint_if_needed(
        agent,
        config,
        reason=reason,
    )
    if message_count is None:
        return False
    logger.warning(
        "chat.checkpoint.repaired_unresolved_tool_calls",
        conversation_id=conversation_id,
        turn_id=turn_id,
        message_count=message_count,
        reason=reason,
    )
    return True


async def repair_turn_checkpoint_on_cancel(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    conversation_id: int,
    turn_id: str,
    turn_start_messages: list[BaseMessage] | None,
    reason: str = "user_cancel",
) -> bool:
    """取消时优先还原 turn 开始快照, 否则修复未完成 tool_calls"""
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
    return await repair_chat_checkpoint_if_needed(
        agent,
        config,
        conversation_id=conversation_id,
        turn_id=turn_id,
        reason=reason,
    )
