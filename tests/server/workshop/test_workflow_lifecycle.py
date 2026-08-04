"""工作流启停调度与删除用例。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.server.workshop.domain.enums import (
    WorkshopWorkflowRunStatus,
)
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleError,
)
from tests.server.workshop.harness import workshop_harness
from tests.server.workshop.workflow_fixtures import single_step_graph


async def _saved_workflow(h, *, name: str = "lifecycle"):
    project = await h.projects.create_project(
        user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
    )
    h.track_project(project.id)
    nodes, edges = single_step_graph()
    draft = await h.schedules.user_draft_workflow(
        project_id=project.id,
        user_id=h.user_id,
        name=name,
        model_key="test-model",
        nodes=nodes,
        edges=edges,
    )
    saved = await h.schedules.user_confirm_save_workflow(
        project_id=project.id, user_id=h.user_id, workflow_id=draft.id
    )
    return project, saved


@pytest.mark.integration
async def test_start_execution_requires_schedule() -> None:
    """无定时时开启执行 fail closed"""
    async with workshop_harness() as h:
        project, saved = await _saved_workflow(h)
        with pytest.raises(WorkshopWorkflowScheduleError, match="no schedule"):
            await h.schedules.start_workflow_execution(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=saved.id,
            )


@pytest.mark.integration
async def test_stop_execution_requires_schedule() -> None:
    """无定时时停止执行 fail closed"""
    async with workshop_harness() as h:
        project, saved = await _saved_workflow(h)
        with pytest.raises(WorkshopWorkflowScheduleError, match="no schedule"):
            await h.schedules.stop_workflow_execution(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=saved.id,
            )


@pytest.mark.integration
async def test_start_and_stop_toggle_schedule_enabled() -> None:
    """开启/停止切换同一工作流下全部 schedule 的 enabled"""
    async with workshop_harness() as h:
        project, saved = await _saved_workflow(h)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 * * * *",
            timezone="UTC",
            authorized_capabilities=(),
            now=now,
        )
        assert schedule.enabled is True
        stopped = await h.schedules.stop_workflow_execution(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
        )
        assert len(stopped) == 1
        assert stopped[0].enabled is False
        assert stopped[0].next_run_at is None
        started = await h.schedules.start_workflow_execution(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            now=now,
        )
        assert len(started) == 1
        assert started[0].enabled is True
        assert started[0].next_run_at is not None


@pytest.mark.integration
async def test_list_saved_workflows_includes_schedule_summary() -> None:
    """列表附带 schedule 摘要字段来源"""
    async with workshop_harness() as h:
        project, saved = await _saved_workflow(h)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="*/5 * * * *",
            timezone="UTC",
            authorized_capabilities=(),
            now=now,
        )
        items = await h.schedules.list_saved_workflows(
            project_id=project.id, user_id=h.user_id
        )
        assert len(items) == 1
        item = items[0]
        assert item.workflow.id == saved.id
        assert item.schedule is not None
        assert item.schedule.enabled is True
        assert item.schedule.cron == "*/5 * * * *"


@pytest.mark.integration
async def test_delete_workflow_cancels_active_runs_and_removes_row() -> None:
    """删除强制取消活跃 run，并移除工作流与 schedule"""
    revoked: list[str] = []
    async with workshop_harness() as h:
        h.schedules._revoke_run = (  # noqa: SLF001
            lambda project_id, user_id, run_id: revoked.append(run_id)
        )
        project, saved = await _saved_workflow(h, name="to-delete")
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 * * * *",
            timezone="UTC",
            authorized_capabilities=(),
            now=now,
        )
        result = await h.schedules.manual_run_with_light_confirm(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            authorized_capabilities=(),
        )
        assert result.run.status is WorkshopWorkflowRunStatus.QUEUED
        await h.schedules.delete_workflow(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
        )
        listed = await h.schedules.list_saved_workflows(
            project_id=project.id, user_id=h.user_id
        )
        assert listed == ()
        with pytest.raises(WorkshopWorkflowScheduleError, match="unknown workflow"):
            await h.schedules.start_workflow_execution(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=saved.id,
            )
        assert result.run.id in revoked
