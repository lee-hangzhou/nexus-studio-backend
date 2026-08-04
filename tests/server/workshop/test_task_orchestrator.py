from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopTaskStatus,
    WorkshopToolCapability,
)
from app.server.workshop.domain.types import ArtifactSubmission
from app.server.workshop.domain.workflow_definition import NodeOutput
from app.server.workshop.persistence.models import WorkshopArtifacts
from app.server.workshop.persistence.repository import WorkshopTaskConflictError
from app.server.workshop.services.task_orchestrator import TaskOrchestratorError
from tests.server.workshop.harness import workshop_harness
from tests.server.workshop.workflow_fixtures import single_step_graph


def _db_artifact(name: str, content: str) -> ArtifactSubmission:
    """构造 DB 存储产物提交"""
    return ArtifactSubmission(
        name=name,
        storage_type=WorkshopArtifactStorageType.DB,
        content=content,
    )


async def _to_reviewing(
    h,
    *,
    project_id: str,
    title: str = "cover A",
    required_artifacts: tuple[str, ...] = (),
    external_auth: tuple[WorkshopToolCapability, ...] = (),
):
    """经工作流创建 executing 任务并推进到 reviewing"""
    task = await h.create_executing_task(
        project_id=project_id,
        title=title,
        required_artifacts=required_artifacts,
        external_auth=external_auth,
    )
    await h.orchestrator.mark_reviewing(
        project_id=project_id, user_id=h.user_id, task_id=task.id
    )
    return task


@pytest.mark.integration
async def test_schedule_trigger_skips_awaiting_go_into_executing() -> None:
    """定时触发基于创建时授权直接执行"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        nodes, edges = single_step_graph(title="publish digest")
        node = replace(
            nodes[0],
            title="publish digest",
            instruction="publish digest",
            outputs=(
                NodeOutput(
                    name="digest.md",
                    storage_type=WorkshopArtifactStorageType.DB,
                ),
            ),
        )
        draft = await h.schedules.agent_draft_workflow(
            project_id=project.id,
            user_id=h.user_id,
            name="weekly digest",
            model_key="test-model",
            nodes=(node,),
            edges=edges,
        )
        saved = await h.schedules.user_confirm_save_workflow(
            project_id=project.id, user_id=h.user_id, workflow_id=draft.id
        )
        schedule = await h.schedules.create_schedule(
            project_id=project.id,
            user_id=h.user_id,
            workflow_id=saved.id,
            cron="0 17 * * 5",
            timezone="Asia/Shanghai",
            authorized_capabilities=(WorkshopToolCapability.CREATE_SCHEDULE,),
        )
        trigger = await h.schedules.trigger_schedule(
            project_id=project.id,
            user_id=h.user_id,
            schedule_id=schedule.id,
            trigger_key=f"run-{uuid4().hex}",
        )
        assert trigger.task.status is WorkshopTaskStatus.EXECUTING
        assert trigger.task.schedule_authorized is True
        assert trigger.task.required_artifacts == ("digest.md",)
        assert WorkshopToolCapability.CREATE_SCHEDULE in trigger.task.external_auth
        assert trigger.run.trigger_key
        assert trigger.event_ids


@pytest.mark.integration
async def test_weak_accept_marks_done_and_reject_reopens_to_realigning() -> None:
    """弱验收完成任务，用户驳回后重新对齐"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        task = await _to_reviewing(
            h,
            project_id=project.id,
            required_artifacts=("report.md",),
        )
        result = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            covered_goals=frozenset({"cover A"}),
            artifacts=(_db_artifact("report.md", "hello"),),
        )
        assert result.passed is True
        done = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert done.status is WorkshopTaskStatus.DONE

        await h.orchestrator.user_reject_done(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        realigned = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert realigned.status is WorkshopTaskStatus.RE_ALIGNING


@pytest.mark.integration
async def test_reject_done_then_reaccept_same_artifact_name_replaces() -> None:
    """DONE->RE_ALIGNING->DONE 同名产物原子替换，能力审计保留"""
    from app.server.workshop.persistence.models import WorkshopTaskCapabilityUses

    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        task = await h.create_executing_task(
            project_id=project.id,
            title="cover A",
            required_artifacts=("report.md",),
            external_auth=(WorkshopToolCapability.BROWSER_WRITE,),
        )
        await h.orchestrator.record_capability_use(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            capability=WorkshopToolCapability.BROWSER_WRITE,
        )
        await h.orchestrator.mark_reviewing(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        first = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            covered_goals=frozenset({"cover A"}),
            artifacts=(_db_artifact("report.md", "v1"),),
        )
        assert first.passed is True
        row_v1 = await WorkshopArtifacts.filter(task_id=task.id, name="report.md").first()
        assert row_v1 is not None
        assert row_v1.metadata["content"] == "v1"
        assert row_v1.storage_key == f"db:{row_v1.id}"
        uses_before = await WorkshopTaskCapabilityUses.filter(task_id=task.id).count()
        assert uses_before == 1

        await h.orchestrator.user_reject_done(
            project_id=project.id, user_id=h.user_id, task_id=task.id
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
        await h.orchestrator.mark_reviewing(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        second = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            covered_goals=frozenset({"cover A"}),
            artifacts=(_db_artifact("report.md", "v2-replaced"),),
        )
        assert second.passed is True
        rows = await WorkshopArtifacts.filter(task_id=task.id, name="report.md").all()
        assert len(rows) == 1
        assert rows[0].id == row_v1.id
        assert rows[0].metadata["content"] == "v2-replaced"
        assert rows[0].storage_key == f"db:{rows[0].id}"
        assert await WorkshopTaskCapabilityUses.filter(task_id=task.id).count() == 1
        done = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert done.status is WorkshopTaskStatus.DONE


@pytest.mark.integration
async def test_weak_accept_required_artifacts_missing_and_free() -> None:
    """必需产物缺失失败；无必需产物可无产物通过"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)

        missing_task = await _to_reviewing(
            h,
            project_id=project.id,
            title="cover A",
            required_artifacts=("report.md",),
        )
        missing = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=missing_task.id,
            covered_goals=frozenset({"cover A"}),
            artifacts=(),
        )
        assert missing.passed is False
        assert "artifacts_missing" in missing.reasons

        free_task = await _to_reviewing(
            h,
            project_id=project.id,
            title="cover C",
            required_artifacts=(),
        )
        free = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=free_task.id,
            covered_goals=frozenset({"cover C"}),
            artifacts=(),
        )
        assert free.passed is True


@pytest.mark.integration
async def test_weak_accept_uses_persisted_capability_usage() -> None:
    """弱验收读取持久化能力使用；未授权失败、已授权通过"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)

        unauthorized = await h.create_executing_task(
            project_id=project.id, title="g"
        )
        await h.orchestrator.record_capability_use(
            project_id=project.id,
            user_id=h.user_id,
            task_id=unauthorized.id,
            capability=WorkshopToolCapability.BROWSER_WRITE,
        )
        await h.orchestrator.mark_reviewing(
            project_id=project.id, user_id=h.user_id, task_id=unauthorized.id
        )
        bad = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=unauthorized.id,
            covered_goals=frozenset({"g"}),
            artifacts=(),
        )
        assert bad.passed is False
        assert "external_auth_violation" in bad.reasons

        authorized = await h.create_executing_task(
            project_id=project.id,
            title="g",
            external_auth=(WorkshopToolCapability.BROWSER_WRITE,),
        )
        await h.orchestrator.record_capability_use(
            project_id=project.id,
            user_id=h.user_id,
            task_id=authorized.id,
            capability=WorkshopToolCapability.BROWSER_WRITE,
        )
        await h.orchestrator.mark_reviewing(
            project_id=project.id, user_id=h.user_id, task_id=authorized.id
        )
        ok = await h.orchestrator.weak_accept(
            project_id=project.id,
            user_id=h.user_id,
            task_id=authorized.id,
            covered_goals=frozenset({"g"}),
            artifacts=(),
        )
        assert ok.passed is True


@pytest.mark.integration
async def test_grant_external_auth_rejects_non_external_capability() -> None:
    """grant_external_auth 拒绝非外部能力"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        task = await h.create_executing_task(
            project_id=project.id, title="x"
        )
        with pytest.raises(TaskOrchestratorError, match="non-external"):
            await h.orchestrator.grant_external_auth(
                project_id=project.id,
                user_id=h.user_id,
                task_id=task.id,
                capabilities=frozenset({WorkshopToolCapability.WEB_SEARCH}),
            )


@pytest.mark.integration
async def test_weak_accept_cas_conflict_leaves_no_partial_artifacts() -> None:
    """弱验收 CAS 冲突时不落库产物"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        task = await _to_reviewing(
            h,
            project_id=project.id,
            required_artifacts=("report.md",),
        )
        reviewing = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        await h.repository.update_task_external_auth_cas(
            project_id=project.id,
            user_id=h.user_id,
            task_id=task.id,
            expected_revision=reviewing.revision,
            expected_status=WorkshopTaskStatus.REVIEWING,
            external_auth=frozenset(),
        )
        stale_revision = reviewing.revision
        with pytest.raises(WorkshopTaskConflictError):
            await h.repository.complete_weak_accept(
                project_id=project.id,
                user_id=h.user_id,
                task_id=task.id,
                expected_revision=stale_revision,
                artifacts=(_db_artifact("report.md", "hello"),),
            )
        assert await WorkshopArtifacts.filter(task_id=task.id).count() == 0
        still_reviewing = await h.orchestrator.get(
            project_id=project.id, user_id=h.user_id, task_id=task.id
        )
        assert still_reviewing.status is WorkshopTaskStatus.REVIEWING


@pytest.mark.integration
async def test_parallel_tasks_can_execute_independently() -> None:
    """同一项目的多个任务可独立并行执行"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        a = await h.create_executing_task(project_id=project.id, title="A")
        b = await h.create_executing_task(project_id=project.id, title="B")
        assert (
            await h.orchestrator.get(
                project_id=project.id, user_id=h.user_id, task_id=a.id
            )
        ).status is WorkshopTaskStatus.EXECUTING
        assert (
            await h.orchestrator.get(
                project_id=project.id, user_id=h.user_id, task_id=b.id
            )
        ).status is WorkshopTaskStatus.EXECUTING


@pytest.mark.integration
async def test_host_attributes_message_or_asks_when_ambiguous() -> None:
    """主持明确归属消息，不确定时要求澄清"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        a = await h.create_executing_task(project_id=project.id, title="A")
        b = await h.create_executing_task(project_id=project.id, title="B")
        attributed = await h.orchestrator.attribute_message(
            project_id=project.id,
            user_id=h.user_id,
            task_id=a.id,
            ambiguous=False,
        )
        assert attributed.task_id == a.id
        assert attributed.needs_clarification is False
        unclear = await h.orchestrator.attribute_message(
            project_id=project.id,
            user_id=h.user_id,
            task_id=None,
            ambiguous=False,
            candidate_task_ids=(a.id, b.id),
        )
        assert unclear.needs_clarification is True
        assert unclear.task_id is None


@pytest.mark.integration
async def test_external_auth_is_per_task() -> None:
    """外部授权仅对当前任务生效"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id, name="p", group_chat_id=h.group_chat_id
        )
        h.track_project(project.id)
        a = await h.create_executing_task(project_id=project.id, title="A")
        b = await h.create_executing_task(project_id=project.id, title="B")
        await h.orchestrator.grant_external_auth(
            project_id=project.id,
            user_id=h.user_id,
            task_id=a.id,
            capabilities=frozenset(
                {
                    WorkshopToolCapability.BROWSER_WRITE,
                    WorkshopToolCapability.MCP,
                }
            ),
        )
        assert (
            await h.orchestrator.has_external_auth(
                project_id=project.id,
                user_id=h.user_id,
                task_id=a.id,
                capability=WorkshopToolCapability.BROWSER_WRITE,
            )
            is True
        )
        assert (
            await h.orchestrator.has_external_auth(
                project_id=project.id,
                user_id=h.user_id,
                task_id=b.id,
                capability=WorkshopToolCapability.BROWSER_WRITE,
            )
            is False
        )
