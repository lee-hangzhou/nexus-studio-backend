"""提案确认/拒绝后丢弃挂起升级邀请 checkpoint"""

from __future__ import annotations

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.runtime.memory_store import get_memory_store
from app.agent.chat.turn.upgrade_invite_emit import has_pending_upgrade_invite
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.turn.message_hygiene import repair_unresolved_tool_calls
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.logger import logger


async def discard_upgrade_invite_checkpoint_after_resolution(
    *,
    user_id: int,
    conversation_id: int,
) -> None:
    """提案已终态后修复 unresolved tool 并写回 checkpoint，避免误 resume"""
    if await has_pending_upgrade_invite_for_conversation(conversation_id):
        raise RuntimeError(
            "refuse discard while upgrade invite proposal still pending"
        )
    conversation = await ChatConversations.get_or_none(
        id=conversation_id, user_id=user_id
    )
    if conversation is None:
        raise RuntimeError("conversation not found for upgrade invite discard")
    model_key = str(conversation.default_model or "").strip()
    if not model_key:
        raise RuntimeError("conversation default_model missing for discard")
    spec = get_model_spec(model_key)
    llm = GatewayChatModel(model_key=model_key, spec=spec)
    agent = build_chat_agent(
        llm,
        [],
        get_chat_checkpointer(),
        store=get_memory_store(),
    )
    config = chat_runnable_config(user_id=user_id, conversation_id=conversation_id)
    if await has_pending_upgrade_invite(agent, config):
        raise RuntimeError("upgrade invite still pending after resolution")
    snap = await agent.aget_state(config)
    messages = list(snap.values.get("messages") or [])
    repaired = repair_unresolved_tool_calls(
        messages,
        reason="upgrade_invite_resolved",
        detail="upgrade invite confirmed or declined; checkpoint discarded",
    )
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]},
    )
    logger.info(
        "chat.checkpoint.upgrade_invite_discarded",
        conversation_id=conversation_id,
        user_id=user_id,
        message_count=len(repaired),
        had_interrupts=bool(snap.interrupts),
    )


async def has_pending_upgrade_invite_for_conversation(conversation_id: int) -> bool:
    """会话是否仍有 pending 升级邀请提案"""
    from app.server.chat.persistence.upgrade_invite_proposals import (
        ChatUpgradeInviteProposals,
    )
    from app.server.workshop.domain.upgrade_invite import UpgradeInviteProposalStatus

    return await ChatUpgradeInviteProposals.filter(
        conversation_id=conversation_id,
        status=UpgradeInviteProposalStatus.PENDING,
    ).exists()
