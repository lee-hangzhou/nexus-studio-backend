from __future__ import annotations

import json
import time
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agent.chat.tools.lc_tools import ChatToolContext, _emit
from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.runtime.ports import get_workshop_port
from app.server.ports.product import (
    WorkshopWorkflowEdgeSpecDTO,
    WorkshopWorkflowNodeSpecDTO,
)
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


def _parse_nodes(
    raw_nodes: list[dict[str, Any]],
) -> tuple[WorkshopWorkflowNodeSpecDTO, ...]:
    """解析工具入参节点列表"""
    if not raw_nodes:
        raise ValueError("nodes required")
    nodes: list[WorkshopWorkflowNodeSpecDTO] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise ValueError("node must be object")
        caps_raw = item.get("external_capabilities") or []
        if not isinstance(caps_raw, list):
            raise ValueError("external_capabilities must be list")
        nodes.append(
            WorkshopWorkflowNodeSpecDTO(
                id=str(item.get("id", "")).strip(),
                title=str(item.get("title", "")).strip(),
                instruction=str(item.get("instruction", "")).strip(),
                preset_key=str(item.get("preset_key", "")).strip(),
                output_name=str(item.get("output_name") or "result").strip(),
                external_capabilities=tuple(str(cap) for cap in caps_raw),
            )
        )
    return tuple(nodes)


def _parse_edges(
    raw_edges: list[dict[str, Any]] | None,
) -> tuple[WorkshopWorkflowEdgeSpecDTO, ...]:
    """解析工具入参边列表"""
    if not raw_edges:
        return ()
    edges: list[WorkshopWorkflowEdgeSpecDTO] = []
    for item in raw_edges:
        if not isinstance(item, dict):
            raise ValueError("edge must be object")
        edges.append(
            WorkshopWorkflowEdgeSpecDTO(
                from_id=str(item.get("from") or item.get("from_id") or "").strip(),
                to_id=str(item.get("to") or item.get("to_id") or "").strip(),
            )
        )
    return tuple(edges)


def build_host_orchestration_tools(
    ctx: ChatToolContext,
    *,
    project_id: str,
) -> list[StructuredTool]:
    """Host 回合：邀请/指定主答 + 工作流草稿/确认/定时/手动跑"""

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

    async def _draft_workflow(
        name: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """起草工坊工作流定义（草稿，未保存）"""
        del tool_call_id
        started = time.perf_counter()
        args = {"name": name, "nodes": nodes, "edges": edges or []}
        model_key = (ctx.model_key or "").strip()
        if not model_key:
            return _emit(
                ctx,
                "draft_workflow",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail="turn model_key required"),
                started,
            )
        try:
            parsed_nodes = _parse_nodes(nodes)
            parsed_edges = _parse_edges(edges)
            workflow = await get_workshop_port().agent_draft_workflow(
                project_id=project_id,
                user_id=ctx.user_id,
                name=name,
                model_key=model_key,
                nodes=parsed_nodes,
                edges=parsed_edges,
            )
        except ValueError as exc:
            return _emit(
                ctx,
                "draft_workflow",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        return _emit(
            ctx,
            "draft_workflow",
            args,
            ToolResult.ok(
                json.dumps(
                    {
                        "workflow_id": workflow.id,
                        "name": workflow.name,
                        "status": workflow.status,
                        "model_key": workflow.model_key,
                        "revision": workflow.revision,
                        "instruction": (
                            "已创建草稿；须用户本回合明确确认后再调用 "
                            "confirm_save_workflow；未确认前禁止建定时或跑一次"
                        ),
                    },
                    ensure_ascii=False,
                )
            ),
            started,
        )

    async def _confirm_save_workflow(
        workflow_id: str,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """用户确认后保存工作流"""
        del tool_call_id
        started = time.perf_counter()
        args = {"workflow_id": workflow_id}
        try:
            workflow = await get_workshop_port().confirm_save_workflow(
                project_id=project_id,
                user_id=ctx.user_id,
                workflow_id=workflow_id,
            )
        except ValueError as exc:
            return _emit(
                ctx,
                "confirm_save_workflow",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        return _emit(
            ctx,
            "confirm_save_workflow",
            args,
            ToolResult.ok(
                json.dumps(
                    {
                        "workflow_id": workflow.id,
                        "status": workflow.status,
                        "instruction": (
                            "已保存；若用户确认建定时则 create_schedule；"
                            "若确认跑一次则 manual_run_workflow"
                        ),
                    },
                    ensure_ascii=False,
                )
            ),
            started,
        )

    async def _create_schedule(
        workflow_id: str,
        cron: str,
        timezone: str = "Asia/Shanghai",
        authorized_capabilities: list[str] | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """对已保存工作流创建定时"""
        del tool_call_id
        started = time.perf_counter()
        caps = list(authorized_capabilities or [])
        args = {
            "workflow_id": workflow_id,
            "cron": cron,
            "timezone": timezone,
            "authorized_capabilities": caps,
        }
        try:
            schedule = await get_workshop_port().create_schedule(
                project_id=project_id,
                user_id=ctx.user_id,
                workflow_id=workflow_id,
                cron=cron,
                timezone=timezone,
                authorized_capabilities=tuple(caps),
            )
        except ValueError as exc:
            return _emit(
                ctx,
                "create_schedule",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        return _emit(
            ctx,
            "create_schedule",
            args,
            ToolResult.ok(
                json.dumps(
                    {
                        "schedule_id": schedule.id,
                        "workflow_id": schedule.workflow_id,
                        "cron": schedule.cron,
                        "timezone": schedule.timezone,
                        "enabled": schedule.enabled,
                    },
                    ensure_ascii=False,
                )
            ),
            started,
        )

    async def _manual_run_workflow(
        workflow_id: str,
        authorized_capabilities: list[str] | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """手动跑一次已保存工作流"""
        del tool_call_id
        started = time.perf_counter()
        caps = list(authorized_capabilities or [])
        args = {
            "workflow_id": workflow_id,
            "authorized_capabilities": caps,
        }
        try:
            run = await get_workshop_port().manual_run_workflow(
                project_id=project_id,
                user_id=ctx.user_id,
                workflow_id=workflow_id,
                authorized_capabilities=tuple(caps),
            )
        except ValueError as exc:
            return _emit(
                ctx,
                "manual_run_workflow",
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        return _emit(
            ctx,
            "manual_run_workflow",
            args,
            ToolResult.ok(
                json.dumps(
                    {
                        "run_id": run.id,
                        "workflow_id": run.workflow_id,
                        "status": run.status,
                    },
                    ensure_ascii=False,
                )
            ),
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
        StructuredTool.from_function(
            coroutine=_draft_workflow,
            name="draft_workflow",
            description=(
                "创建工坊「工作流定义」草稿（不是写本地脚本、不是 OS cron）。"
                "用户要定时/自动化任务时优先调用本工具。"
                "nodes 每项含 id/title/instruction/preset_key，可选 output_name、"
                "external_capabilities；edges 项含 from/to。"
                "preset_key 必须来自可邀请目录。"
                "执行成功条件是工作区交付文件，不是聊天消息。"
                "创建后须用户明确确认再 confirm_save_workflow；"
                "禁止用写文件工具替代定时。"
            ),
        ),
        StructuredTool.from_function(
            coroutine=_confirm_save_workflow,
            name="confirm_save_workflow",
            description=(
                "将草稿工作流确认为已保存。"
                "仅当用户本回合明确同意保存该工作流后调用；"
                "workflow_id 来自 draft_workflow。"
            ),
        ),
        StructuredTool.from_function(
            coroutine=_create_schedule,
            name="create_schedule",
            description=(
                "为已保存工作流创建产品内定时（Celery Beat），不是本机 launchd/cron。"
                "仅当用户本回合明确同意创建定时后调用。"
                "cron 为 5 段表达式，如每 5 分钟用 */5 * * * *；"
                "timezone 默认 Asia/Shanghai；"
                "若节点声明了外部能力，authorized_capabilities 必须覆盖。"
            ),
        ),
        StructuredTool.from_function(
            coroutine=_manual_run_workflow,
            name="manual_run_workflow",
            description=(
                "对已保存工作流排队跑一次（异步执行平面，不写入聊天时间线）。"
                "仅当用户本回合明确同意立即执行后调用；"
                "不要用来代替 create_schedule。"
            ),
        ),
    ]
