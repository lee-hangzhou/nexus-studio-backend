from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.database import db
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopEventKind,
    WorkshopTaskStatus,
    WorkshopToolCapability,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.domain.types import (
    ArtifactSubmission,
    ScheduleStartedPayload,
    WorkflowStep,
)
from app.server.workshop.persistence.models import WorkshopProjects
from app.server.workshop.persistence.repository import (
    WorkshopOwnershipError,
    WorkshopRepository,
    WorkshopTaskConflictError,
    WorkshopWorkflowNotFoundError,
    new_id,
)
from app.server.workshop.domain.ecommerce.profiles import ECOM_PRESET_KEYS
from app.server.workshop.domain.presets import presets_for_keys
from tests.server.workshop.harness import workshop_harness


@pytest.mark.integration
async def test_workshop_repository_persists_project_roster_and_task_cas() -> None:
    """本地 PostgreSQL 持久化项目、名册与任务 CAS"""
    await db.connect()
    repository = WorkshopRepository()
    suffix = uuid4().hex
    project_id = f"wp_{suffix}"
    chat = await ChatConversations.create(
        user_id=1,
        title=f"workshop-{suffix}",
        default_model="test",
        status=ChatConversationStatus.ACTIVE,
    )
    try:
        project = await repository.create_project_with_presets(
            project_id=project_id,
            user_id=1,
            name="本地持久化测试",
            group_chat_id=int(chat.id),
            presets=presets_for_keys(ECOM_PRESET_KEYS),
            bootstrap_keys=list(ECOM_PRESET_KEYS),
        )
        assert project.id == project_id
        assert len(await repository.list_roster(project_id=project_id, user_id=1)) == 6

        task = await repository.create_task(
            task_id=f"wt_{suffix}",
            project_id=project_id,
            user_id=1,
            title="生成周报",
            goals=("抓取", "汇总"),
            status=WorkshopTaskStatus.ALIGNING,
        )
        updated = await repository.update_task_status_cas(
            project_id=project_id,
            user_id=1,
            task_id=task.id,
            expected_revision=1,
            expected_status=WorkshopTaskStatus.ALIGNING,
            target_status=WorkshopTaskStatus.AWAITING_GO,
        )
        assert updated.status is WorkshopTaskStatus.AWAITING_GO
        assert updated.revision == 2

        with pytest.raises(WorkshopTaskConflictError):
            await repository.update_task_status_cas(
                project_id=project_id,
                user_id=1,
                task_id=task.id,
                expected_revision=1,
                expected_status=WorkshopTaskStatus.ALIGNING,
                target_status=WorkshopTaskStatus.AWAITING_GO,
            )
    finally:
        await WorkshopProjects.filter(id=project_id, user_id=1).delete()
        await ChatConversations.filter(id=chat.id, user_id=1).delete()
        await db.disconnect()


@pytest.mark.integration
async def test_workshop_project_rejects_other_user_group_chat() -> None:
    """复合外键映射为类型化归属错误，禁止裸 IntegrityError"""
    await db.connect()
    repository = WorkshopRepository()
    suffix = uuid4().hex
    project_id = f"wp_{suffix}"
    owner_chat = await ChatConversations.create(
        user_id=101,
        title=f"owner-{suffix}",
        default_model="test",
        status=ChatConversationStatus.ACTIVE,
    )
    try:
        from app.server.workshop.persistence.repository import (
            WorkshopGroupChatOwnershipError,
        )

        with pytest.raises(WorkshopGroupChatOwnershipError):
            await repository.create_project_with_presets(
                project_id=project_id,
                user_id=202,
                name="越权绑定",
                group_chat_id=int(owner_chat.id),
            )
        assert await WorkshopProjects.filter(id=project_id).exists() is False
    finally:
        await WorkshopProjects.filter(id=project_id).delete()
        await ChatConversations.filter(id=owner_chat.id).delete()
        await db.disconnect()


@pytest.mark.integration
async def test_create_artifact_rejects_cross_project_task_id() -> None:
    """跨项目 task_id 创建产物 fail closed"""
    async with workshop_harness() as h:
        project_a = await h.projects.create_project(
            user_id=h.user_id, name="a", group_chat_id=h.group_chat_id
        )
        h.track_project(project_a.id)
        other_chat_id = await h.create_extra_group_chat()
        project_b = await h.projects.create_project(
            user_id=h.user_id, name="b", group_chat_id=other_chat_id
        )
        h.track_project(project_b.id)
        task = await h.repository.create_task(
            task_id=f"wt_{uuid4().hex}",
            project_id=project_a.id,
            user_id=h.user_id,
            title="t",
            goals=("g",),
            status=WorkshopTaskStatus.EXECUTING,
        )
        with pytest.raises(WorkshopOwnershipError):
            await h.repository.create_artifact(
                artifact_id=new_id("art"),
                project_id=project_b.id,
                user_id=h.user_id,
                task_id=task.id,
                artifact=ArtifactSubmission(
                    name="report.md",
                    storage_type=WorkshopArtifactStorageType.DB,
                    content="hello",
                ),
            )


@pytest.mark.integration
async def test_append_event_rejects_cross_project_task_id() -> None:
    """跨项目 task_id 追加事件 fail closed"""
    async with workshop_harness() as h:
        project_a = await h.projects.create_project(
            user_id=h.user_id, name="a", group_chat_id=h.group_chat_id
        )
        h.track_project(project_a.id)
        other_chat_id = await h.create_extra_group_chat()
        project_b = await h.projects.create_project(
            user_id=h.user_id, name="b", group_chat_id=other_chat_id
        )
        h.track_project(project_b.id)
        task = await h.repository.create_task(
            task_id=f"wt_{uuid4().hex}",
            project_id=project_a.id,
            user_id=h.user_id,
            title="t",
            goals=("g",),
            status=WorkshopTaskStatus.EXECUTING,
        )
        with pytest.raises(WorkshopOwnershipError):
            await h.repository.append_event(
                event_key=new_id("evt"),
                project_id=project_b.id,
                user_id=h.user_id,
                kind=WorkshopEventKind.SCHEDULE_STARTED,
                payload=ScheduleStartedPayload(
                    schedule_id="sched_x",
                    task_id=task.id,
                    trigger_key="tk-1",
                    message="start",
                ),
                task_id=task.id,
            )


@pytest.mark.integration
async def test_get_workflow_rejects_cross_project() -> None:
    """跨项目读取工作流 fail closed"""
    async with workshop_harness() as h:
        project_a = await h.projects.create_project(
            user_id=h.user_id, name="a", group_chat_id=h.group_chat_id
        )
        h.track_project(project_a.id)
        other_chat_id = await h.create_extra_group_chat()
        project_b = await h.projects.create_project(
            user_id=h.user_id, name="b", group_chat_id=other_chat_id
        )
        h.track_project(project_b.id)
        workflow = await h.repository.create_workflow(
            workflow_id=new_id("wf"),
            project_id=project_a.id,
            user_id=h.user_id,
            name="flow",
            steps=(WorkflowStep(title="step"),),
            status=WorkshopWorkflowStatus.SAVED,
            source=WorkshopWorkflowSource.AGENT,
        )
        with pytest.raises(WorkshopWorkflowNotFoundError):
            await h.repository.get_workflow(
                project_id=project_b.id,
                user_id=h.user_id,
                workflow_id=workflow.id,
            )


@pytest.mark.integration
async def test_create_schedule_rejects_cross_project_workflow() -> None:
    """跨项目 workflow_id 创建定时 fail closed"""
    async with workshop_harness() as h:
        project_a = await h.projects.create_project(
            user_id=h.user_id, name="a", group_chat_id=h.group_chat_id
        )
        h.track_project(project_a.id)
        other_chat_id = await h.create_extra_group_chat()
        project_b = await h.projects.create_project(
            user_id=h.user_id, name="b", group_chat_id=other_chat_id
        )
        h.track_project(project_b.id)
        workflow = await h.repository.create_workflow(
            workflow_id=new_id("wf"),
            project_id=project_a.id,
            user_id=h.user_id,
            name="flow",
            steps=(WorkflowStep(title="step"),),
            status=WorkshopWorkflowStatus.SAVED,
            source=WorkshopWorkflowSource.USER,
        )
        with pytest.raises(WorkshopWorkflowNotFoundError):
            await h.repository.create_schedule(
                schedule_id=new_id("sched"),
                project_id=project_b.id,
                user_id=h.user_id,
                workflow_id=workflow.id,
                cron="0 9 * * *",
                timezone_name="UTC",
                authorized_at=datetime.now(timezone.utc),
                authorized_external_capabilities=frozenset(
                    {WorkshopToolCapability.CREATE_SCHEDULE}
                ),
            )
