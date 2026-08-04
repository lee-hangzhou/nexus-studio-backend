from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Sequence
from uuid import uuid4

from tortoise import Tortoise

from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.database import db
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)
from app.server.workshop.domain.types import WorkshopTaskRecord
from app.server.workshop.domain.workflow_definition import (
    NodeAssignee,
    NodeOutput,
    WorkflowNode,
)
from app.server.workshop.persistence.models import WorkshopProjects
from app.server.workshop.persistence.repository import WorkshopRepository
from app.server.workshop.services.task_orchestrator import WorkshopTaskOrchestrator
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleService,
)
from app.server.workshop.services.workshop_project_service import WorkshopProjectService


@dataclass(slots=True)
class WorkshopTestHarness:
    """工坊集成测试装配与清理登记"""

    user_id: int
    group_chat_id: int
    repository: WorkshopRepository
    projects: WorkshopProjectService
    orchestrator: WorkshopTaskOrchestrator
    schedules: WorkshopWorkflowScheduleService
    project_ids: list[str]
    chat_ids: list[int]

    def track_project(self, project_id: str) -> None:
        """登记待清理项目"""
        self.project_ids.append(project_id)

    async def create_extra_group_chat(self) -> int:
        """为本用户再创建一条群聊供第二项目绑定"""
        suffix = uuid4().hex
        chat = await ChatConversations.create(
            user_id=self.user_id,
            title=f"workshop-extra-{suffix}",
            default_model="test",
            status=ChatConversationStatus.ACTIVE,
        )
        chat_id = int(chat.id)
        self.chat_ids.append(chat_id)
        return chat_id

    async def create_executing_task(
        self,
        *,
        project_id: str,
        title: str = "step",
        required_artifacts: Sequence[str] = (),
        external_auth: Sequence[WorkshopToolCapability] = (),
        preset_key: str = "ecom_market_competitor_advisor",
    ) -> WorkshopTaskRecord:
        """经工作流保存后创建 executing 壳任务（无自由立任务路径）"""
        outputs = tuple(
            NodeOutput(
                name=name,
                storage_type=WorkshopArtifactStorageType.DB,
            )
            for name in required_artifacts
        )
        if not outputs:
            outputs = (
                NodeOutput(
                    name=f"{title}_out",
                    storage_type=WorkshopArtifactStorageType.DB,
                    required=False,
                ),
            )
        nodes = (
            WorkflowNode(
                id="n1",
                title=title,
                instruction=title,
                assignee=NodeAssignee(preset_key=preset_key),
                outputs=outputs,
            ),
        )
        draft = await self.schedules.user_draft_workflow(
            project_id=project_id,
            user_id=self.user_id,
            name=title,
            model_key="test-model",
            nodes=nodes,
            edges=(),
        )
        saved = await self.schedules.user_confirm_save_workflow(
            project_id=project_id,
            user_id=self.user_id,
            workflow_id=draft.id,
        )
        return await self.repository.create_executing_task_from_workflow(
            project_id=project_id,
            user_id=self.user_id,
            workflow_id=saved.id,
            task_id=str(uuid4()),
            external_auth=tuple(external_auth),
        )


@asynccontextmanager
async def workshop_harness(
    *,
    user_id: int = 1,
) -> AsyncIterator[WorkshopTestHarness]:
    """连接本地库并装配工坊服务；退出时清理本轮群聊与级联项目"""
    await db.connect()
    await Tortoise.get_connection("default").execute_query(
        "ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS selected_expert_key VARCHAR(128)"
    )
    repository = WorkshopRepository()
    projects = WorkshopProjectService(repository)
    orchestrator = WorkshopTaskOrchestrator(repository)
    schedules = WorkshopWorkflowScheduleService(
        repository=repository,
        orchestrator=orchestrator,
    )
    suffix = uuid4().hex
    chat = await ChatConversations.create(
        user_id=user_id,
        title=f"workshop-{suffix}",
        default_model="test",
        status=ChatConversationStatus.ACTIVE,
    )
    harness = WorkshopTestHarness(
        user_id=user_id,
        group_chat_id=int(chat.id),
        repository=repository,
        projects=projects,
        orchestrator=orchestrator,
        schedules=schedules,
        project_ids=[],
        chat_ids=[int(chat.id)],
    )
    try:
        yield harness
    finally:
        for project_id in harness.project_ids:
            await WorkshopProjects.filter(id=project_id, user_id=user_id).delete()
        for chat_id in harness.chat_ids:
            await ChatConversations.filter(id=chat_id, user_id=user_id).delete()
        await db.disconnect()
