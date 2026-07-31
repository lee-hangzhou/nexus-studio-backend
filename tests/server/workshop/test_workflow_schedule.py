from __future__ import annotations

from uuid import uuid4

import pytest

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopEventKind,
    WorkshopTaskStatus,
    WorkshopToolCapability,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.domain.types import ArtifactSubmission, WorkflowStep
from app.server.workshop.persistence.models import WorkshopTasks
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleError,
)
from tests.server.workshop.harness import workshop_harness


def _db_artifact(name: str, content: str) -> ArtifactSubmission:
    """构造 DB 存储产物提交"""
    return ArtifactSubmission(
        name=name,
        storage_type=WorkshopArtifactStorageType.DB,
        content=content,
    )


@pytest.mark.integration
async def test_agent_draft_requires_user_confirm_to_save() -> None:
    """Agent 草稿必须经用户确认才能保存和运行"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"), WorkflowStep(title="summarize")),
        )
        assert draft.status is WorkshopWorkflowStatus.DRAFT
        assert draft.source is WorkshopWorkflowSource.AGENT
        assert await h.schedules.list_saved_workflows(
            project_id=project.id, user_id=h.user_id
        ) == ()
        with pytest.raises(WorkshopWorkflowScheduleError):
            await h.schedules.manual_run_with_light_confirm(
                project_id=project.id, user_id=h.user_id, workflow_id=draft.id
            )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        assert saved.status is WorkshopWorkflowStatus.SAVED
        assert saved.source is WorkshopWorkflowSource.AGENT
        assert (
            len(
                await h.schedules.list_saved_workflows(
                    project_id=project.id, user_id=h.user_id
                )
            )
            == 1
        )


@pytest.mark.integration
async def test_user_authored_workflow_can_be_saved() -> None:
    """用户自编工作流可确认保存"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.user_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="手工流",
            steps=(WorkflowStep(title="a"),),
        )
        assert draft.source is WorkshopWorkflowSource.USER
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        assert saved.name == "手工流"
        assert saved.source is WorkshopWorkflowSource.USER
        assert saved.steps == (WorkflowStep(title="a"),)


@pytest.mark.integration
async def test_manual_run_creates_task_with_light_confirmation() -> None:
    """手动运行工作流以轻确认创建任务并携带步骤必需产物"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(
                WorkflowStep(
                    title="collect",
                    required_artifact_names=("digest.md",),
                ),
            ),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        run = await h.schedules.manual_run_with_light_confirm(
            project_id=project.id, user_id=h.user_id, workflow_id=saved.id
        )
        assert run.task.status is WorkshopTaskStatus.EXECUTING
        assert run.used_light_confirmation is True
        assert run.task.required_artifacts == ("digest.md",)


@pytest.mark.integration
async def test_create_schedule_authorizes_future_triggers_without_new_auth() -> None:
    """创建定时即固化外部能力，触发时复制进任务且携带必需产物"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(
                WorkflowStep(
                    title="collect",
                    required_artifact_names=("digest.md",),
                    external_capabilities=(WorkshopToolCapability.BROWSER_WRITE,),
                ),
            ),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        authorized = frozenset(
            {
                WorkshopToolCapability.CREATE_SCHEDULE,
                WorkshopToolCapability.BROWSER_WRITE,
            }
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 17 * * 5",
            timezone="Asia/Shanghai",
            authorized_capabilities=tuple(authorized),
        )
        assert schedule.timezone == "Asia/Shanghai"
        assert schedule.authorized_at is not None
        assert schedule.authorized_external_capabilities == authorized

        trigger_key = f"tk-{uuid4().hex}"
        trigger = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=trigger_key,
        )
        assert trigger.requires_external_auth_popup is False
        assert trigger.task.status is WorkshopTaskStatus.EXECUTING
        assert trigger.task.schedule_authorized is True
        assert trigger.task.external_auth == authorized
        assert trigger.task.required_artifacts == ("digest.md",)
        assert trigger.run.trigger_key == f"manual:{trigger_key}"
        events = await h.schedules.list_events(
            project_id=project.id, user_id=h.user_id
        )
        assert any(event.kind is WorkshopEventKind.SCHEDULE_STARTED for event in events)
        assert trigger.event_ids


@pytest.mark.integration
async def test_schedule_success_publishes_events_failure_pins() -> None:
    """定时成功发布事件，失败任务在唤醒时置顶"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(
                WorkflowStep(
                    title="collect",
                    required_artifact_names=("digest.md",),
                ),
            ),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 17 * * 5",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
        )
        trigger = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=f"ok-{uuid4().hex}",
        )
        await h.orchestrator.record_capability_use(
            project_id=project.id,
            user_id=h.user_id,
            task_id=trigger.task.id,
            capability=WorkshopToolCapability.CREATE_SCHEDULE,
        )
        ok = await h.schedules.complete_scheduled_run(
            project_id=project.id,
            user_id=h.user_id,
            task_id=trigger.task.id,
            covered_goals=frozenset(trigger.task.goals),
            artifacts=(_db_artifact("digest.md", "ok"),),
        )
        assert ok.task_status is WorkshopTaskStatus.DONE
        kinds = {
            event.kind
            for event in await h.schedules.list_events(
                project_id=project.id, user_id=h.user_id
            )
        }
        assert WorkshopEventKind.SCHEDULE_SUMMARY in kinds
        assert WorkshopEventKind.ARTIFACTS_PUBLISHED in kinds

        trigger2 = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=f"bad-{uuid4().hex}",
        )
        bad = await h.schedules.complete_scheduled_run(
            project_id=project.id,
            user_id=h.user_id,
            task_id=trigger2.task.id,
            covered_goals=frozenset(),
            artifacts=(),
        )
        assert bad.task_status is WorkshopTaskStatus.BLOCKED
        dash = await h.projects.wake_dashboard(
            project_id=project.id, user_id=h.user_id
        )
        assert trigger2.task.id in dash.pinned_task_ids


@pytest.mark.integration
async def test_duplicate_trigger_key_returns_existing_run_deterministically() -> None:
    """重复 trigger_key 幂等返回已有运行，不产生第二任务或第二开始事件"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
        )
        trigger_key = f"dup-{uuid4().hex}"
        first = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=trigger_key,
        )
        second = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=trigger_key,
        )
        assert second.task.id == first.task.id
        assert second.run.id == first.run.id
        assert second.event_ids == first.event_ids
        assert await WorkshopTasks.filter(project_id=project.id).count() == 1
        started = [
            event
            for event in await h.schedules.list_events(
                project_id=project.id, user_id=h.user_id
            )
            if event.kind is WorkshopEventKind.SCHEDULE_STARTED
        ]
        assert len(started) == 1


@pytest.mark.integration
async def test_create_schedule_persists_next_run_at_from_injected_now() -> None:
    """创建定时用注入时钟计算并持久化 next_run_at，不依赖真实时间"""
    from datetime import datetime, timezone

    fixed_now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=fixed_now,
        )
        assert schedule.authorized_at == fixed_now
        assert schedule.next_run_at == datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)


@pytest.mark.integration
async def test_two_competing_claims_produce_one_run() -> None:
    """两个并发 claim 只会成功一次并只产生一条运行"""
    import asyncio
    from datetime import datetime, timezone

    from app.server.workshop.persistence.models import WorkshopScheduleRuns
    from app.server.workshop.persistence.repository import new_id

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        )
        assert schedule.next_run_at is not None
        expected = schedule.next_run_at

        async def _claim() -> object:
            """发起一次到期 claim"""
            return await h.repository.claim_due_schedule_run(
                schedule_id=schedule.id,
                expected_next_run_at=expected,
                task_id=str(uuid4()),
                started_event_key=new_id("evt"),
            )

        first, second = await asyncio.gather(_claim(), _claim())
        successes = [item for item in (first, second) if item is not None]
        assert len(successes) == 1
        assert await WorkshopScheduleRuns.filter(schedule_id=schedule.id).count() == 1
        assert await WorkshopTasks.filter(project_id=project.id).count() == 1
        refreshed = await h.repository.get_schedule(
            project_id=project.id, user_id=h.user_id, schedule_id=schedule.id
        )
        assert refreshed.next_run_at == datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)

@pytest.mark.integration
async def test_tick_once_claims_due_and_advances_next_run_at() -> None:
    """tick_once 领取到期定时并推进 next_run_at；丢失 CAS 不计成功"""
    from datetime import datetime, timezone

    from app.server.workshop.persistence.models import WorkshopScheduleRuns

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        )
        claimed = await h.schedules.tick_once(
            now=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
            batch_size=10,
        )
        assert len(claimed) == 1
        assert claimed[0].run.schedule_id == schedule.id
        assert await WorkshopScheduleRuns.filter(schedule_id=schedule.id).count() == 1
        refreshed = await h.repository.get_schedule(
            project_id=project.id, user_id=h.user_id, schedule_id=schedule.id
        )
        assert refreshed.next_run_at == datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)

        empty = await h.schedules.tick_once(
            now=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
            batch_size=10,
        )
        assert empty == ()
        assert await WorkshopScheduleRuns.filter(schedule_id=schedule.id).count() == 1


@pytest.mark.integration
async def test_create_schedule_rejects_incomplete_external_scope() -> None:
    """定时授权必须覆盖工作流步骤声明的全部外部能力"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="需浏览器",
            steps=(
                WorkflowStep(
                    title="browse",
                    external_capabilities=(
                        WorkshopToolCapability.BROWSER_WRITE,
                        WorkshopToolCapability.MCP,
                    ),
                ),
            ),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        with pytest.raises(
            WorkshopWorkflowScheduleError,
            match="cover workflow external scope",
        ):
            await h.schedules.create_schedule(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=saved.id,
                cron="0 9 * * *",
                timezone="UTC",
                authorized_capabilities=(WorkshopToolCapability.BROWSER_WRITE,),
            )


@pytest.mark.integration
async def test_manual_trigger_namespaces_key_and_does_not_consume_next_run_at() -> None:
    """手动 run-now 使用 manual: 命名空间，且不推进 next_run_at"""
    from datetime import datetime, timezone

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        )
        before = schedule.next_run_at
        caller_key = f"now-{uuid4().hex}"
        first = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=caller_key,
        )
        assert first.run.trigger_key == f"manual:{caller_key}"
        second = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=caller_key,
        )
        assert second.run.id == first.run.id
        refreshed = await h.schedules.get_schedule(
            project_id=project.id, user_id=h.user_id, schedule_id=schedule.id
        )
        assert refreshed.next_run_at == before


@pytest.mark.integration
async def test_complete_scheduled_run_rejects_manual_task() -> None:
    """普通/手动任务不得走 complete_scheduled_run"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        run = await h.schedules.manual_run_with_light_confirm(
            project_id=project.id, user_id=h.user_id, workflow_id=saved.id
        )
        assert run.task.schedule_id is None
        assert run.task.schedule_authorized is False
        with pytest.raises(
            WorkshopWorkflowScheduleError,
            match="schedule_id and schedule_authorized",
        ):
            await h.schedules.complete_scheduled_run(
                project_id=project.id,
                user_id=h.user_id,
                task_id=run.task.id,
                covered_goals=frozenset(run.task.goals),
                artifacts=(),
            )


@pytest.mark.integration
async def test_manual_run_requires_and_persists_authorized_capabilities() -> None:
    """手动跑校验外部授权范围并写入执行任务"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="需浏览器",
            steps=(
                WorkflowStep(
                    title="browse",
                    external_capabilities=(WorkshopToolCapability.BROWSER_WRITE,),
                ),
            ),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        with pytest.raises(
            WorkshopWorkflowScheduleError,
            match="cover workflow external scope",
        ):
            await h.schedules.manual_run_with_light_confirm(
                project_id=project.id,
                user_id=h.user_id,
                workflow_id=saved.id,
                authorized_capabilities=(),
            )
        run = await h.schedules.manual_run_with_light_confirm(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            authorized_capabilities=(WorkshopToolCapability.BROWSER_WRITE,),
        )
        assert WorkshopToolCapability.BROWSER_WRITE in run.task.external_auth


@pytest.mark.integration
async def test_schedule_get_list_enable_disable_and_claim_gate() -> None:
    """定时读写与启停：禁用清空 next_run_at 且不可 claim；启用重算"""
    import asyncio
    from datetime import datetime, timezone

    from app.server.workshop.persistence.models import WorkshopScheduleRuns
    from app.server.workshop.services.workflow_schedule_service import (
        WorkshopWorkflowScheduleError,
    )

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="日报",
            steps=(WorkflowStep(title="collect"),),
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 9 * * *",
            timezone="UTC",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
            now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        )
        listed = await h.schedules.list_schedules(
            project_id=project.id, user_id=h.user_id
        )
        assert [item.id for item in listed] == [schedule.id]
        got = await h.schedules.get_schedule(
            project_id=project.id, user_id=h.user_id, schedule_id=schedule.id
        )
        assert got.next_run_at == datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)

        disabled = await h.schedules.disable_schedule(
            project_id=project.id, user_id=h.user_id, schedule_id=schedule.id
        )
        assert disabled.enabled is False
        assert disabled.next_run_at is None
        claimed = await h.repository.claim_due_schedule_run(
            schedule_id=schedule.id,
            expected_next_run_at=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
            task_id=str(uuid4()),
            started_event_key=f"evt_{uuid4().hex}",
        )
        assert claimed is None
        assert await WorkshopScheduleRuns.filter(schedule_id=schedule.id).count() == 0

        enabled = await h.schedules.enable_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        )
        assert enabled.enabled is True
        assert enabled.next_run_at == datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)

        overdue = await h.schedules.enable_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            now=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        )
        assert overdue.next_run_at == datetime(
            2026, 7, 30, 9, 0, tzinfo=timezone.utc
        )

        async def _enable() -> object:
            """并发启用"""
            return await h.schedules.enable_schedule(
                project_id=project.id,
                user_id=h.user_id,
                schedule_id=schedule.id,
                now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
            )

        first, second = await asyncio.gather(_enable(), _enable())
        assert first.next_run_at == second.next_run_at

        other = await h.create_extra_group_chat()
        foreign = await h.projects.create_project(
            user_id=h.user_id, name="other", group_chat_id=other
        )
        h.track_project(foreign.id)
        with pytest.raises(WorkshopWorkflowScheduleError, match="unknown schedule"):
            await h.schedules.get_schedule(
                project_id=foreign.id, user_id=h.user_id, schedule_id=schedule.id
            )


@pytest.mark.integration
async def test_workflow_service_does_not_import_canvas_agent() -> None:
    """工坊工作流不依赖画布 Agent"""
    import app.server.workshop.services.workflow_schedule_service as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "app.agent.canvas" not in source
    assert "canvas.workflow" not in source
