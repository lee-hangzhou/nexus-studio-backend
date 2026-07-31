from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Sequence
from uuid import uuid4

from tortoise import Tortoise

from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.database import db
from app.server.workshop.domain.types import WorkshopTaskRecord
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

    async def propose_and_confirm_task(
        self,
        *,
        project_id: str,
        title: str,
        goals: Sequence[str],
        required_artifacts: Sequence[str] = (),
    ) -> WorkshopTaskRecord:
        """测试专用：显式提议再确认立任务，替代生产 confirm_create_task 捷径"""
        proposal = await self.orchestrator.host_propose_create_task(
            project_id=project_id,
            user_id=self.user_id,
            title=title,
            goals=goals,
            required_artifacts=required_artifacts,
        )
        return await self.orchestrator.user_confirm_create_task(
            project_id=project_id,
            user_id=self.user_id,
            proposal_id=proposal.id,
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
