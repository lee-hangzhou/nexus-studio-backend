from __future__ import annotations

import time
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agent.chat.tools.lc_tools import ChatToolContext, _emit
from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.runtime.ports import get_workshop_port
from app.server.workshop.domain.upgrade_invite import (
    UpgradeInviteProposalError,
    validate_invite_expert_keys,
)


class InvitedExpertItem(BaseModel):
    """邀请成功的专家项"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    expert_id: str = Field(min_length=1)


class InviteExpertsResult(BaseModel):
    """invite_experts 结构化结果"""

    model_config = ConfigDict(extra="forbid")

    invited: list[InvitedExpertItem] = Field(min_length=1)
    primary_expert_id: str = Field(min_length=1)
    primary_expert_key: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    instruction: str = Field(min_length=1)


class DesignateSpeakerResult(BaseModel):
    """designate_speaker 结构化结果"""

    model_config = ConfigDict(extra="forbid")

    expert_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)


def build_host_orchestration_tools(
    ctx: ChatToolContext,
    *,
    project_id: str,
) -> list[StructuredTool]:
    """Host 回合可调用的邀请与指定主答工具"""

    async def _invite_experts(
        expert_keys: list[str],
        primary_expert_key: str,
        rationale: str,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """邀请专家进房并隐式指定主答"""
        del tool_call_id
        started = time.perf_counter()
        args = {
            "expert_keys": expert_keys,
            "primary_expert_key": primary_expert_key,
            "rationale": rationale,
        }
        try:
            validated_keys = validate_invite_expert_keys(
                expert_keys=tuple(expert_keys),
                primary_expert_key=primary_expert_key,
            )
        except UpgradeInviteProposalError as exc:
            return _emit(
                ctx,
                "invite_experts",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        cleaned_rationale = rationale.strip()
        if not cleaned_rationale:
            return _emit(
                ctx,
                "invite_experts",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail="rationale required"),
                started,
            )
        primary = primary_expert_key.strip()

        workshop = get_workshop_port()
        invited: list[InvitedExpertItem] = []
        primary_id: str | None = None
        for key in validated_keys:
            expert = await workshop.add_preset_to_roster(
                project_id=project_id,
                user_id=ctx.user_id,
                preset_key=key,
            )
            await workshop.invite_to_room(
                project_id=project_id,
                user_id=ctx.user_id,
                expert_id=expert.id,
            )
            invited.append(
                InvitedExpertItem(key=key, name=expert.name, expert_id=expert.id)
            )
            if key == primary:
                primary_id = expert.id

        if primary_id is None:
            return _emit(
                ctx,
                "invite_experts",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail="primary expert not invited"),
                started,
            )
        ctx.pending_expert_handoff_id = primary_id
        payload = InviteExpertsResult(
            invited=invited,
            primary_expert_id=primary_id,
            primary_expert_key=primary,
            rationale=cleaned_rationale,
            instruction=(
                "向用户说明基于什么考虑邀请了谁、如何协作；"
                "不要说「请某某专家回答」；随后系统将隐式让主答专家接话"
            ),
        )
        return _emit(
            ctx,
            "invite_experts",
            args,
            ToolResult.ok(payload.model_dump_json()),
            started,
        )

    async def _designate_speaker(
        expert_id: str,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """隐式指定在场专家接本回合"""
        del tool_call_id
        started = time.perf_counter()
        workshop = get_workshop_port()
        room = await workshop.room_members(
            project_id=project_id, user_id=ctx.user_id
        )
        if expert_id not in room:
            return _emit(
                ctx,
                "designate_speaker",
                {"expert_id": expert_id},
                ToolResult.fail(
                    INVALID_ARGUMENTS, detail="expert not in room; invite first"
                ),
                started,
            )
        ctx.pending_expert_handoff_id = expert_id
        payload = DesignateSpeakerResult(
            expert_id=expert_id,
            instruction="勿向用户宣告转交；系统将隐式让该专家接话",
        )
        return _emit(
            ctx,
            "designate_speaker",
            {"expert_id": expert_id},
            ToolResult.ok(payload.model_dump_json()),
            started,
        )

    return [
        StructuredTool.from_function(
            coroutine=_invite_experts,
            name="invite_experts",
            description=(
                "立即邀请一名或多名产品专家进入工坊房间，无需用户确认；"
                "expert_keys 为可邀请目录 preset_key；"
                "primary_expert_key 须在 expert_keys 内，成功后由其隐式接答；"
                "成功后向用户说明为何邀请谁、如何协作；"
                "禁止说「请某某回答」"
            ),
        ),
        StructuredTool.from_function(
            coroutine=_designate_speaker,
            name="designate_speaker",
            description=(
                "隐式指定已在房间内的专家回答本回合用户消息；"
                "expert_id 为名册/房间成员 id；"
                "不要在对话中宣告转交"
            ),
        ),
    ]
