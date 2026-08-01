from __future__ import annotations

import pytest

from app.agent.runtime.stream.frames import StreamFrameType
from app.agent.runtime.turn_engine.events import ModelToken, TurnEnded
from app.agent.runtime.turn_engine.sse_subscriber import SseTurnSubscriber
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.workshop.host_context import build_host_turn_context_block
from app.agent.workshop.mount import (
    WorkshopTurnMountContext,
    prepare_turn_tool_snapshot,
    resolve_thread_id,
)
from app.server.chat.domain.stream_enums import TokenChannel
from app.server.workshop.domain.enums import WorkshopExpertKind
from app.server.workshop.domain.ecommerce.effective_profile import workshop_thread_id
from tests.server.workshop.harness import workshop_harness


def test_host_context_contains_roster_and_room_not_alone() -> None:
    """Host 上下文含名册与房间成员，避免「就我一个」"""
    block = build_host_turn_context_block(
        roster_names=("市场与竞品研究", "商品策划与文案"),
        room_member_names=("市场与竞品研究",),
        task_titles=("竞品调研",),
        invite_directory_names=("营销活动策划", "经营分析与复盘"),
        task_assignee_lines=("竞品调研：市场与竞品研究",),
    )
    assert "市场与竞品研究" in block
    assert "商品策划与文案" in block
    assert "竞品调研" in block
    assert "营销活动策划" in block
    assert "任务负责人" in block
    assert "不得声称" in block
    assert "房间在场" in block or "在场" in block


def test_expert_task_thread_id_isolation() -> None:
    """专家×任务 thread_id 彼此隔离；专家闲聊与 Host idle 隔离"""
    a = workshop_thread_id("wp_1", "expert_a", "task_1")
    b = workshop_thread_id("wp_1", "expert_a", "task_2")
    c = workshop_thread_id("wp_1", "expert_b", "task_1")
    assert len({a, b, c}) == 3
    ctx = WorkshopTurnMountContext(
        project_id="wp_1",
        expert_id="expert_a",
        task_id="task_1",
        preset_key="ecom_market_competitor_advisor",
        expert_kind=WorkshopExpertKind.ADVISOR,
    )
    assert resolve_thread_id(ctx) == a
    idle_expert = WorkshopTurnMountContext(
        project_id="wp_1",
        expert_id="expert_a",
        task_id=None,
        preset_key="ecom_market_competitor_advisor",
        expert_kind=WorkshopExpertKind.ADVISOR,
        is_host=False,
    )
    host = WorkshopTurnMountContext(
        project_id="wp_1",
        expert_id="host",
        task_id=None,
        preset_key=None,
        expert_kind=WorkshopExpertKind.ADVISOR,
        is_host=True,
    )
    assert resolve_thread_id(idle_expert) != resolve_thread_id(host)
    assert "expert:expert_a:idle" in resolve_thread_id(idle_expert)
    assert "host:idle" in resolve_thread_id(host)


@pytest.mark.asyncio
async def test_roster_room_assignments_are_independent() -> None:
    """名册、房间成员、任务分配三者独立"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="indep",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=tuple(
                key
                for key in (
                    "ecom_market_competitor_advisor",
                    "ecom_listing_planner_executor",
                    "ecom_campaign_planner_executor",
                    "ecom_ops_analytics_executor",
                )
            ),
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(project_id=project.id, user_id=h.user_id)
        assert len(roster) >= 4
        expert = roster[0]
        members_before = await h.projects.room_members(
            project_id=project.id, user_id=h.user_id
        )
        assert expert.id not in members_before

        await h.projects.invite_to_room(
            project_id=project.id, user_id=h.user_id, expert_id=expert.id
        )
        members_after = await h.projects.room_members(
            project_id=project.id, user_id=h.user_id
        )
        assert expert.id in members_after

        task = await h.propose_and_confirm_task(
            project_id=project.id,
            title="t1",
            goals=["g1"],
        )
        assignments_before = await h.orchestrator.list_task_assignments(
            project_id=project.id, user_id=h.user_id
        )
        assert all(expert.id not in row.expert_ids for row in assignments_before)

        await h.projects.assign_to_task(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            expert_id=expert.id,
        )
        assignments_after = await h.orchestrator.list_task_assignments(
            project_id=project.id, user_id=h.user_id
        )
        matched = next(row for row in assignments_after if row.task_id == task.id)
        assert expert.id in matched.expert_ids


@pytest.mark.asyncio
async def test_sse_frames_include_speaker_attribution_when_set() -> None:
    """SSE 帧携带 speaker_role/expert_id/expert_name/avatar/task_id"""
    frames: list = []

    async def emit(frame) -> None:
        """测试用事件发射"""
        frames.append(frame)

    sub = SseTurnSubscriber(
        policy=SseTerminalPolicy(emit_done_on_completed=True),
        sse_attribution={
            "speaker_role": "advisor",
            "expert_id": "expert_1",
            "expert_name": "市场与竞品研究",
            "avatar": "/avatars/experts/ecom-market.png",
            "task_id": "task_1",
        },
    )
    await sub.handle(
        ModelToken(turn_id="t1", step_index=0, channel=TokenChannel.ANSWER, text="hi"),
        emit=emit,
    )
    await sub.handle(
        TurnEnded(turn_id="t1", terminated_by=TurnTerminatedBy.COMPLETED),
        emit=emit,
    )
    token_frame = frames[0]
    done_frame = frames[1]
    assert token_frame.speaker_role == "advisor"
    assert token_frame.expert_id == "expert_1"
    assert token_frame.expert_name == "市场与竞品研究"
    assert token_frame.avatar == "/avatars/experts/ecom-market.png"
    assert token_frame.task_id == "task_1"
    assert done_frame.expert_id == "expert_1"
    assert done_frame.expert_name == "市场与竞品研究"
    assert token_frame.type == StreamFrameType.TOKEN


@pytest.mark.asyncio
async def test_workshop_mount_prepare_turn_host_has_invite_tools_not_taobao() -> None:
    """Host 编排工具为 invite_experts / designate_speaker，无 taobao 写"""
    from app.agent.workshop.host_tools import build_host_orchestration_tools
    from app.agent.chat.tools.lc_tools import ChatToolContext
    from pathlib import Path

    ctx = WorkshopTurnMountContext(
        project_id="wp_test",
        expert_id="host",
        task_id=None,
        preset_key=None,
        expert_kind=WorkshopExpertKind.ADVISOR,
        is_host=True,
        host_context_block="名册：市场与竞品研究",
        model_key="",
    )
    tool_ctx = ChatToolContext(
        user_id=1,
        conversation_id=1,
        workspace=Path("/tmp"),
        audit=[],
    )
    tools = build_host_orchestration_tools(tool_ctx, project_id="wp_test")
    names = {tool.name for tool in tools}
    assert "invite_experts" in names
    assert "designate_speaker" in names
    assert "taobao_store_write" not in names
    prompt = build_host_turn_context_block(
        roster_names=("市场与竞品研究",),
        room_member_names=(),
        task_titles=(),
        invite_directory_names=(),
    )
    assert "市场与竞品研究" in prompt
