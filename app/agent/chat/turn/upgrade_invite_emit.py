from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.server.infra.config import settings


class UpgradeInviteExpertItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)


class UpgradeInviteInterrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upgrade_invite: bool = True
    proposal_id: int = Field(ge=1)
    conversation_id: int = Field(ge=1)
    expert_keys: list[str] = Field(min_length=1)
    primary_expert_key: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    experts: list[UpgradeInviteExpertItem] = Field(min_length=1)


def _interrupt_payload(intr: object) -> object:
    """从 LangGraph Interrupt 取出载荷；形状不对则 fail closed"""
    if isinstance(intr, Interrupt):
        return intr.value
    raise TypeError(f"unexpected interrupt type: {type(intr)!r}")


def _parse_upgrade_invite_interrupt(value: object) -> UpgradeInviteInterrupt | None:
    """识别并校验升级邀请 interrupt；非本类型返回 None，契约破坏则抛错"""
    if not isinstance(value, dict) or not value.get("upgrade_invite"):
        return None
    try:
        return UpgradeInviteInterrupt.model_validate(value)
    except ValidationError as exc:
        raise ValueError("upgrade invite interrupt violates contract") from exc


def interrupt_value_is_upgrade_invite(value: object) -> bool:
    """是否为升级邀请 interrupt"""
    return _parse_upgrade_invite_interrupt(value) is not None


async def has_pending_upgrade_invite(
    agent: CompiledStateGraph, config: RunnableConfig
) -> bool:
    """是否仍有待用户确认的升级邀请；以提案表为准，interrupt 仅作定位"""
    from app.server.chat.persistence.upgrade_invite_proposals import (
        ChatUpgradeInviteProposals,
    )
    from app.server.workshop.domain.upgrade_invite import UpgradeInviteProposalStatus

    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    for intr in snap.interrupts:
        proposal = _parse_upgrade_invite_interrupt(_interrupt_payload(intr))
        if proposal is None:
            continue
        row = await ChatUpgradeInviteProposals.get_or_none(id=proposal.proposal_id)
        if row is not None and row.status == UpgradeInviteProposalStatus.PENDING:
            return True
    return False


async def emit_upgrade_invite_proposals(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    emit: Callable[[StreamFrame], Awaitable[None]],
    *,
    turn_id: str,
) -> bool:
    """将仍 pending 的升级邀请 interrupt 发成 SSE；已终态提案跳过"""
    from app.server.chat.persistence.upgrade_invite_proposals import (
        ChatUpgradeInviteProposals,
    )
    from app.server.workshop.domain.upgrade_invite import UpgradeInviteProposalStatus

    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    emitted = False
    for intr in snap.interrupts:
        proposal = _parse_upgrade_invite_interrupt(_interrupt_payload(intr))
        if proposal is None:
            continue
        row = await ChatUpgradeInviteProposals.get_or_none(id=proposal.proposal_id)
        if row is None or row.status != UpgradeInviteProposalStatus.PENDING:
            continue
        await emit(
            create_stream_frame(
                type=StreamFrameType.UPGRADE_INVITE_PROPOSED,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                turn_id=turn_id,
                proposal_id=proposal.proposal_id,
                conversation_id=proposal.conversation_id,
                expert_keys=proposal.expert_keys,
                primary_expert_key=proposal.primary_expert_key,
                rationale=proposal.rationale,
                experts=[item.model_dump(mode="python") for item in proposal.experts],
            )
        )
        emitted = True
    return emitted
