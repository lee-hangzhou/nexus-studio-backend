"""工作流 DAG 草稿/确认/手动跑/定时入队集成测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.server.workshop.domain.enums import (
    WorkshopToolCapability,
    WorkshopWorkflowRunStatus,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.persistence.models import WorkshopSchedules
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleError,
)
from tests.server.workshop.harness import workshop_harness
from tests.server.workshop.workflow_fixtures import (
    linear_research_copy_graph,
    single_step_graph,
)


@pytest.mark.integration
async def test_agent_draft_requires_user_confirm_to_save() -> None:
    """Agent 草稿须确认后才能跑"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        nodes, edges = linear_research_copy_graph()
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            model_key="test-model",
            nodes=nodes,
            edges=edges,
        )
        assert draft.status is WorkshopWorkflowStatus.DRAFT
        assert draft.source is WorkshopWorkflowSource.AGENT
        with pytest.raises(WorkshopWorkflowScheduleError):
            await h.schedules.manual_run_with_light_confirm(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=draft.id,
                authorized_capabilities=(WorkshopToolCapability.WEB_SEARCH,),
            )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        assert saved.status is WorkshopWorkflowStatus.SAVED
        assert len(saved.nodes) == 2


@pytest.mark.integration
async def test_manual_run_creates_queued_workflow_run() -> None:
    """手动跑创建 queued 运行记录与同 id 执行壳任务"""
    enqueued: list[str] = []

    async with workshop_harness() as h:
        h.schedules._enqueue_run = (  # noqa: SLF001 — 测试注入入队
            lambda project_id, user_id, run_id: enqueued.append(run_id)
        )
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        nodes, edges = single_step_graph()
        draft = await h.schedules.user_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="单步",
            model_key="test-model",
            nodes=nodes,
            edges=edges,
        )
        await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        result = await h.schedules.manual_run_with_light_confirm(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=draft.id,
            authorized_capabilities=(),
        )
        assert result.run.status is WorkshopWorkflowRunStatus.QUEUED
        assert result.run.id in enqueued
        task = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=result.run.id
        )
        assert task.id == result.run.id


@pytest.mark.integration
async def test_create_schedule_and_tick_enqueues_workflow_run() -> None:
    """创建定时后 tick 会 claim 并入队 workflow_run"""
    enqueued: list[str] = []
    async with workshop_harness() as h:
        h.schedules._enqueue_run = (  # noqa: SLF001
            lambda project_id, user_id, run_id: enqueued.append(run_id)
        )
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        nodes, edges = single_step_graph()
        draft = await h.schedules.user_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="定时流",
            model_key="test-model",
            nodes=nodes,
            edges=edges,
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 * * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=now,
        )
        assert schedule.next_run_at is not None
        await WorkshopSchedules.filter(id=schedule.id).update(
            next_run_at=now - timedelta(minutes=1)
        )
        claimed = await h.schedules.tick_once(now=now, batch_size=10)
        assert len(claimed) == 1
        assert claimed[0].workflow_run is not None
        assert claimed[0].workflow_run.id in enqueued
