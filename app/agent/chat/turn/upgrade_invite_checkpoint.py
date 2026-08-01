"""升级邀请 interrupt 终态：写回卫生 checkpoint，避免挂起占用生成锁"""

from __future__ import annotations

from langchain_core.messages import RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.turn.upgrade_invite_emit import has_pending_upgrade_invite
from app.agent.runtime.turn.message_hygiene import repair_unresolved_tool_calls
from app.server.infra.logger import logger


async def finalize_upgrade_invite_checkpoint(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    *,
    conversation_id: int,
    turn_id: str,
) -> bool:
    """将升级邀请挂起回合收束为可继续的 checkpoint；无挂起则返回 False"""
    if not await has_pending_upgrade_invite(agent, config):
        return False
    snap = await agent.aget_state(config)
    messages = list(snap.values.get("messages") or [])
    repaired = repair_unresolved_tool_calls(
        messages,
        reason="upgrade_invite_awaiting_confirm",
        detail="upgrade invite proposed; awaiting user confirm or decline",
    )
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]},
    )
    logger.info(
        "chat.checkpoint.upgrade_invite_finalized",
        conversation_id=conversation_id,
        turn_id=turn_id,
        message_count=len(repaired),
    )
    return True
