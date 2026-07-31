from __future__ import annotations

from uuid import uuid4

import pytest

from app.server.workshop.domain.enums import (
    WorkshopExpertKind,
    WorkshopRole,
    WorkshopTaskStatus,
    WorkshopToolCapability,
)
from app.server.workshop.domain.ecommerce.profiles import ECOM_PRESET_KEYS
from app.server.workshop.domain.presets import ECOM_PRESET_EXPERTS
from app.server.workshop.services.workshop_project_service import WorkshopProjectError
from tests.server.workshop.harness import workshop_harness


@pytest.mark.integration
async def test_create_workshop_project_has_exactly_one_group_chat_and_host() -> None:
    """工坊项目只有一个群聊且主持常驻"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="投放工坊",
            group_chat_id=h.group_chat_id,
        )
        h.track_project(project.id)
        assert project.group_chat_id == h.group_chat_id
        assert project.host_role is WorkshopRole.HOST
        loaded = await h.projects.get_project(
            project_id=project.id, user_id=h.user_id
        )
        assert loaded.group_chat_id == project.group_chat_id
        with pytest.raises(WorkshopProjectError):
            await h.projects.attach_second_group_chat(
                project_id=project.id, user_id=h.user_id
            )


@pytest.mark.integration
async def test_preset_roster_has_six_experts_and_presets_are_readonly() -> None:
    """initial_expert_keys 初始化六个只读预置专家"""
    assert len(ECOM_PRESET_EXPERTS) == 6
    kinds = {expert.kind for expert in ECOM_PRESET_EXPERTS}
    assert WorkshopExpertKind.ADVISOR in kinds
    assert WorkshopExpertKind.EXECUTOR in kinds

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="p",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=ECOM_PRESET_KEYS,
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert {expert.preset_key for expert in roster if expert.preset_key} == {
            expert.key for expert in ECOM_PRESET_EXPERTS
        }
        with pytest.raises(WorkshopProjectError):
            await h.projects.mutate_preset_in_place(
                project_id=project.id,
                user_id=h.user_id,
                preset_key="ecom_market_competitor_advisor",
                name="hacked",
            )
        copy = await h.projects.copy_preset_to_custom(
            project_id=project.id,
            user_id=h.user_id,
            preset_key="ecom_market_competitor_advisor",
            name="我的竞品研究",
        )
        assert copy.preset_key is None
        assert copy.name == "我的竞品研究"
        assert copy.source_preset_key == "ecom_market_competitor_advisor"


@pytest.mark.integration
async def test_host_proposed_custom_expert_requires_user_confirm() -> None:
    """主持提议定制专家后需用户确认入库"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        proposal_id = await h.projects.host_propose_custom_expert(
            project_id=project.id,
            user_id=h.user_id,
            name="竞品顾问",
            kind=WorkshopExpertKind.ADVISOR,
        )
        roster_before = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert all(expert.name != "竞品顾问" for expert in roster_before)
        expert = await h.projects.user_confirm_custom_expert(
            project_id=project.id, user_id=h.user_id, proposal_id=proposal_id
        )
        assert expert.name == "竞品顾问"
        roster_after = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert any(item.id == expert.id for item in roster_after)


@pytest.mark.integration
async def test_user_can_decline_custom_expert_proposal() -> None:
    """用户可拒绝定制专家提议"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        proposal_id = await h.projects.host_propose_custom_expert(
            project_id=project.id,
            user_id=h.user_id,
            name="拒绝顾问",
            kind=WorkshopExpertKind.ADVISOR,
        )
        await h.projects.user_decline_custom_expert(
            project_id=project.id, user_id=h.user_id, proposal_id=proposal_id
        )
        with pytest.raises(WorkshopProjectError):
            await h.projects.user_confirm_custom_expert(
                project_id=project.id, user_id=h.user_id, proposal_id=proposal_id
            )


@pytest.mark.integration
async def test_concurrent_invite_assign_and_capability_use_are_idempotent() -> None:
    """并发邀请/分配/能力记录幂等成功"""
    import asyncio

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="p",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=("ecom_market_competitor_advisor",),
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        expert = next(
            item
            for item in roster
            if item.preset_key == "ecom_market_competitor_advisor"
        )
        task = await h.propose_and_confirm_task(
            project_id=project.id, title="t", goals=("g",)
        )
        await h.orchestrator.host_propose_go(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        await h.orchestrator.user_confirm_go(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        await h.orchestrator.begin_execution(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )

        async def _invite() -> None:
            """并发邀请入房"""
            await h.projects.invite_to_room(
                project_id=project.id, user_id=h.user_id, expert_id=expert.id
            )

        async def _assign() -> None:
            """并发分配任务专家"""
            await h.projects.assign_to_task(
                project_id=project.id,
                user_id=h.user_id,
                task_id=task.id,
                expert_id=expert.id,
            )

        async def _cap() -> None:
            """并发记录能力使用"""
            await h.orchestrator.record_capability_use(
                project_id=project.id,
                user_id=h.user_id,
                task_id=task.id,
                capability=WorkshopToolCapability.BROWSER_WRITE,
            )

        await asyncio.gather(_invite(), _invite(), _invite())
        await asyncio.gather(_assign(), _assign(), _assign())
        await asyncio.gather(_cap(), _cap(), _cap())
        members = await h.projects.room_members(
            project_id=project.id, user_id=h.user_id
        )
        assert expert.id in members
        assignments = await h.projects.task_assignments(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert expert.id in assignments
        used = await h.repository.list_capability_uses(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert WorkshopToolCapability.BROWSER_WRITE in used


@pytest.mark.integration
async def test_invite_to_room_from_roster() -> None:
    """项目名册专家可被邀请进入群聊"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="p",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=("ecom_market_competitor_advisor",),
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        expert = next(
            item
            for item in roster
            if item.preset_key == "ecom_market_competitor_advisor"
        )
        await h.projects.invite_to_room(
            project_id=project.id, user_id=h.user_id, expert_id=expert.id
        )
        members = await h.projects.room_members(
            project_id=project.id, user_id=h.user_id
        )
        assert expert.id in members


@pytest.mark.integration
async def test_upgrade_single_agent_becomes_host_without_task_proposal() -> None:
    """单 Agent 升级后成为主持，不自动推送任务提议"""
    async with workshop_harness() as h:
        upgraded = await h.projects.confirm_upgrade_to_project(
            user_id=h.user_id,
            group_chat_id=h.group_chat_id,
            project_name="竞品周报项目",
            carried_message_count=1,
        )
        h.track_project(upgraded.project.id)
        assert upgraded.project.host_role is WorkshopRole.HOST
        assert upgraded.group_chat_id == h.group_chat_id
        assert upgraded.carried_message_count == 1
        assert upgraded.pending_task_proposal is None
        proposals = await h.repository.list_pending_task_proposals(
            project_id=upgraded.project.id, user_id=h.user_id
        )
        assert proposals == []


@pytest.mark.integration
async def test_wake_dashboard_pins_blocked_and_realigning_tasks() -> None:
    """唤醒仪表盘置顶阻塞与重新对齐任务"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        executing = await h.repository.create_task(
            task_id=f"wt_exec_{uuid4().hex}",
            project_id=project.id,
            user_id=h.user_id,
            title="exec",
            goals=("g",),
            status=WorkshopTaskStatus.EXECUTING,
        )
        blocked = await h.repository.create_task(
            task_id=f"wt_blocked_{uuid4().hex}",
            project_id=project.id,
            user_id=h.user_id,
            title="blocked",
            goals=("g",),
            status=WorkshopTaskStatus.BLOCKED,
        )
        realigning = await h.repository.create_task(
            task_id=f"wt_realign_{uuid4().hex}",
            project_id=project.id,
            user_id=h.user_id,
            title="realign",
            goals=("g",),
            status=WorkshopTaskStatus.RE_ALIGNING,
        )
        dash = await h.projects.wake_dashboard(
            project_id=project.id, user_id=h.user_id
        )
        assert dash.group_chat_id == project.group_chat_id
        assert set(dash.pinned_task_ids) == {blocked.id, realigning.id}
        assert executing.id not in dash.pinned_task_ids


@pytest.mark.integration
async def test_kick_from_task_does_not_remove_from_roster() -> None:
    """任务离场与移出项目名册相互独立"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="p",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=("ecom_listing_planner_executor",),
        )
        h.track_project(project.id)
        task = await h.repository.create_task(
            task_id=f"wt_{uuid4().hex}",
            project_id=project.id,
            user_id=h.user_id,
            title="t1",
            goals=("g",),
            status=WorkshopTaskStatus.ALIGNING,
        )
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        expert = next(
            item
            for item in roster
            if item.preset_key == "ecom_listing_planner_executor"
        )
        await h.projects.assign_to_task(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            expert_id=expert.id,
        )
        await h.projects.kick_from_task(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            expert_id=expert.id,
        )
        assignments = await h.projects.task_assignments(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert expert.id not in assignments
        roster_after_kick = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert any(item.id == expert.id for item in roster_after_kick)
        await h.projects.remove_from_roster(
            project_id=project.id, user_id=h.user_id, expert_id=expert.id
        )
        roster_after_remove = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert all(item.id != expert.id for item in roster_after_remove)


@pytest.mark.integration
async def test_workshop_project_id_is_not_creative_project_namespace() -> None:
    """工坊项目 id 与创作 Project 命名空间隔离"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        assert project.id.startswith("wp_")
