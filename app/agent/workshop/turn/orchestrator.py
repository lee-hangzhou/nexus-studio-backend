from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.agent.runtime.skills.prompt_format import format_selected_skill_bodies_text
from app.agent.runtime.turn.runner import stream_agent_turn
from app.agent.workshop.host_context import build_host_turn_context_block
from app.agent.workshop.mount import WORKSHOP_MOUNT, WorkshopTurnMountContext
from app.agent.workshop.room_timeline import RoomTimelineMessage, build_room_timeline_block
from app.contracts.workshop import WorkshopTurnTarget
from app.server.chat.domain.enums import ChatMessageRole
from app.server.chat.persistence.messages import ChatMessages
from app.server.ports.product import SelectedSkillDTO
from app.server.workshop.domain.enums import WorkshopExpertKind
from app.server.workshop.domain.expert_catalog import get_catalog_entry
from app.server.workshop.domain.presets import list_invite_directory
from app.server.workshop.services.task_orchestrator import WorkshopTaskOrchestrator
from app.server.workshop.services.workshop_project_service import WorkshopProject, WorkshopProjectService


def _avatar_url_for_preset(preset_key: str | None) -> str | None:
    """按 preset 解析专家头像 URL"""
    if not preset_key:
        return None
    try:
        entry = get_catalog_entry(preset_key)
    except KeyError:
        return f"/avatars/experts/{preset_key.replace('_', '-')}.png"
    return f"/avatars/experts/{entry.avatar_id}.png"


def _speaker_name_from_metadata(metadata: object) -> str | None:
    """从消息 metadata 读取发言者显示名"""
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("expert_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    role = metadata.get("speaker_role")
    if role == "host":
        return "项目助手"
    return None


async def _load_room_timeline_block(*, conversation_id: int) -> str:
    """加载群聊可见对话（用户/助手），不含 tool 轨迹"""
    rows = (
        await ChatMessages.filter(conversation_id=conversation_id)
        .order_by("created_at", "id")
        .limit(80)
    )
    user_role = int(ChatMessageRole.USER)
    assistant_role = int(ChatMessageRole.ASSISTANT)
    items: list[RoomTimelineMessage] = []
    for row in rows:
        if row.role == user_role:
            items.append(RoomTimelineMessage(role="user", content=str(row.content or "")))
        elif row.role == assistant_role:
            items.append(
                RoomTimelineMessage(
                    role="assistant",
                    content=str(row.content or ""),
                    speaker_name=_speaker_name_from_metadata(row.metadata),
                )
            )
    return build_room_timeline_block(items)


async def _host_context_block(
    *,
    projects: WorkshopProjectService,
    orchestrator: WorkshopTaskOrchestrator,
    project: WorkshopProject,
    user_id: int,
) -> str:
    """读取名册、房间、任务、负责人与可邀请目录并组装 Host 上下文"""
    roster = await projects.list_roster(project_id=project.id, user_id=user_id)
    room_ids = await projects.room_members(project_id=project.id, user_id=user_id)
    roster_by_id = {expert.id: expert for expert in roster}
    tasks = await orchestrator.list_tasks(project_id=project.id, user_id=user_id)
    assignments = await orchestrator.list_task_assignments(
        project_id=project.id, user_id=user_id
    )
    task_by_id = {task.id: task for task in tasks}
    assignee_lines: list[str] = []
    for row in assignments:
        task = task_by_id.get(row.task_id)
        if task is None or not row.expert_ids:
            continue
        names = tuple(
            roster_by_id[expert_id].name
            for expert_id in row.expert_ids
            if expert_id in roster_by_id
        )
        if names:
            assignee_lines.append(f"{task.title}：{'、'.join(names)}")
    invite_names = tuple(
        entry.name
        for entry in list_invite_directory()
        if entry.key not in {expert.preset_key for expert in roster if expert.preset_key}
    )
    return build_host_turn_context_block(
        roster_names=tuple(expert.name for expert in roster),
        room_member_names=tuple(
            roster_by_id[expert_id].name for expert_id in sorted(room_ids) if expert_id in roster_by_id
        ),
        task_titles=tuple(task.title for task in tasks),
        invite_directory_names=invite_names,
        task_assignee_lines=tuple(assignee_lines),
    )


async def stream_workshop_turn(
    *,
    projects: WorkshopProjectService,
    orchestrator: WorkshopTaskOrchestrator,
    project: WorkshopProject,
    user_id: int,
    conversation_id: int,
    turn_id: str,
    content: str,
    model_key: str,
    enable_tools: bool,
    cancel_event: asyncio.Event,
    turn_target: WorkshopTurnTarget | None = None,
    selected_skills: tuple[SelectedSkillDTO, ...] | None = None,
    persist_user_message: bool = True,
) -> AsyncIterator[str]:
    """工坊群聊 turn：host idle 或在场专家；未进房专家 fail closed"""
    target = turn_target or WorkshopTurnTarget()
    is_host = target.expert_id is None
    preset_key: str | None = None
    expert_kind = WorkshopExpertKind.ADVISOR
    expert_id = target.expert_id or "host"
    expert_name = "项目助手"
    avatar_url = "/avatars/experts/host.png"
    task_id = target.task_id
    granted_external = frozenset()
    host_context_block = ""
    if is_host:
        host_context_block = await _host_context_block(
            projects=projects,
            orchestrator=orchestrator,
            project=project,
            user_id=user_id,
        )
    else:
        roster = await projects.list_roster(project_id=project.id, user_id=user_id)
        room_ids = await projects.room_members(project_id=project.id, user_id=user_id)
        matched = next((item for item in roster if item.id == target.expert_id), None)
        if matched is None:
            raise ValueError(f"expert not on roster: {target.expert_id}")
        if matched.id not in room_ids:
            raise ValueError(f"expert not in room: {target.expert_id}")
        preset_key = matched.preset_key
        expert_kind = matched.kind
        expert_id = matched.id
        expert_name = matched.name
        avatar_url = _avatar_url_for_preset(preset_key) or "/avatars/experts/general-writer.png"
        if task_id:
            task = await orchestrator.get(
                project_id=project.id, user_id=user_id, task_id=task_id
            )
            granted_external = frozenset(task.external_auth)

    selected_skills_text = ""
    if selected_skills:
        selected_skills_text = format_selected_skill_bodies_text(selected_skills)

    persist_chat = target.persist_chat_messages
    room_timeline_block = ""
    if persist_chat:
        room_timeline_block = await _load_room_timeline_block(
            conversation_id=conversation_id
        )

    ctx = WorkshopTurnMountContext(
        project_id=project.id,
        expert_id=expert_id,
        task_id=task_id,
        preset_key=preset_key,
        expert_kind=expert_kind,
        granted_external=granted_external,
        is_host=is_host,
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        cancel_event=cancel_event,
        model_key=model_key,
        enable_tools=enable_tools,
        content=content,
        host_context_block=host_context_block,
        room_timeline_block=room_timeline_block,
        speaker_role=target.speaker_role or ("host" if is_host else "expert"),
        expert_name=expert_name,
        avatar_url=avatar_url,
        selected_skills_text=selected_skills_text,
        persist_user_message=persist_chat and persist_user_message,
        persist_chat_messages=persist_chat,
    )
    async for chunk in stream_agent_turn(WORKSHOP_MOUNT, ctx):
        yield chunk

    handoff_id = (
        ctx.tool_ctx.pending_expert_handoff_id
        if ctx.tool_ctx is not None
        else None
    )
    if is_host and handoff_id:
        # 隐式交接：同一用户原话由指定在场专家续答，不重复落库用户消息
        handoff_target = WorkshopTurnTarget(
            expert_id=handoff_id,
            task_id=task_id,
            speaker_role="expert",
            persist_user_message=False,
        )
        async for chunk in stream_workshop_turn(
            projects=projects,
            orchestrator=orchestrator,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=f"{turn_id}-handoff",
            content=content,
            model_key=model_key,
            enable_tools=enable_tools,
            cancel_event=cancel_event,
            turn_target=handoff_target,
            selected_skills=selected_skills,
            persist_user_message=False,
        ):
            yield chunk
