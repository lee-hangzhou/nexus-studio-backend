from __future__ import annotations

from typing import Iterable, Optional, Sequence
from uuid import uuid4

from app.server.workshop.domain.enums import WorkshopTaskStatus, WorkshopToolCapability
from app.server.workshop.domain.external_tool_gate import is_external_capability
from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.workshop.domain.types import (
    ArtifactRecord,
    ArtifactSubmission,
    MessageAttribution,
    TaskAssignmentRecord,
    WeakAcceptResult,
    WorkshopTaskRecord,
)
from app.server.workshop.domain.weak_accept import evaluate_weak_accept
from app.server.workshop.persistence.repository import (
    WorkshopRepository,
    WorkshopTaskConflictError,
    WorkshopTaskNotFoundError,
)


class TaskOrchestratorError(Exception):
    """非法任务迁移或门闩违规（fail closed）"""


class WorkshopTaskOrchestrator:
    """工坊任务编排应用服务；产品状态仅经仓储读写"""

    def __init__(self, repository: WorkshopRepository) -> None:
        """注入工坊仓储"""
        self._repository = repository

    async def get(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """按 id 读取任务；不存在则失败"""
        try:
            return await self._repository.require_task(
                project_id=project_id, user_id=user_id, task_id=task_id
            )
        except WorkshopTaskNotFoundError as exc:
            raise TaskOrchestratorError(f"unknown task: {task_id}") from exc

    async def list_tasks(
        self, *, project_id: str, user_id: int
    ) -> tuple[WorkshopTaskRecord, ...]:
        """列出项目全部任务"""
        tasks = await self._repository.list_tasks_for_project(
            project_id=project_id, user_id=user_id
        )
        return tuple(tasks)

    async def list_artifacts(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        """列出项目产物，可按 task_id 过滤"""
        artifacts = await self._repository.list_artifacts_for_project(
            project_id=project_id,
            user_id=user_id,
            task_id=task_id,
        )
        return tuple(artifacts)

    
    async def list_authorized_operations(
        self, *, project_id: str, user_id: int
    ) -> tuple[AuthorizedOperation, ...]:
        """列出项目 authorized_operations"""
        return await self._repository.list_authorized_operations_for_project(
            project_id=project_id, user_id=user_id
        )

    async def list_task_assignments(
        self, *, project_id: str, user_id: int
    ) -> tuple[TaskAssignmentRecord, ...]:
        """列出项目全部任务的专家分配"""
        assignments = await self._repository.list_task_assignments_for_project(
            project_id=project_id, user_id=user_id
        )
        return tuple(assignments)

    
    async def host_propose_go(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """主持提议可以执行"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status not in (
            WorkshopTaskStatus.ALIGNING,
            WorkshopTaskStatus.RE_ALIGNING,
        ):
            raise TaskOrchestratorError(f"cannot propose go from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.AWAITING_GO,
        )

    async def user_confirm_go(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """用户确认可以执行"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.AWAITING_GO:
            raise TaskOrchestratorError(f"cannot confirm go from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.AUTHORIZED,
        )

    async def begin_execution(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """进入执行态；必须已获用户 go 确认"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.AUTHORIZED:
            raise TaskOrchestratorError(
                f"cannot begin execution from {task.status.value}"
            )
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.EXECUTING,
        )

    async def mark_blocked(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """执行中阻塞（等人/授权/补充信息）"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.EXECUTING:
            raise TaskOrchestratorError(f"cannot block from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.BLOCKED,
        )

    async def mark_re_aligning(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """翻车或验收驳回后重新对齐"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status not in (
            WorkshopTaskStatus.EXECUTING,
            WorkshopTaskStatus.BLOCKED,
            WorkshopTaskStatus.REVIEWING,
        ):
            raise TaskOrchestratorError(f"cannot re-align from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.RE_ALIGNING,
        )

    async def mark_reviewing(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """进入弱验收"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.EXECUTING:
            raise TaskOrchestratorError(f"cannot review from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.REVIEWING,
        )

    async def cancel(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """取消未终态任务"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status in (
            WorkshopTaskStatus.DONE,
            WorkshopTaskStatus.FAILED,
            WorkshopTaskStatus.CANCELLED,
        ):
            raise TaskOrchestratorError(f"cannot cancel from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.CANCELLED,
        )

    async def fail(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """标记任务失败终态"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status not in (
            WorkshopTaskStatus.EXECUTING,
            WorkshopTaskStatus.BLOCKED,
            WorkshopTaskStatus.REVIEWING,
        ):
            raise TaskOrchestratorError(f"cannot fail from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.FAILED,
        )

    async def record_capability_use(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        capability: WorkshopToolCapability,
    ) -> None:
        """记录任务实际使用过的工具能力；仅执行/阻塞态可写"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status not in (
            WorkshopTaskStatus.EXECUTING,
            WorkshopTaskStatus.BLOCKED,
        ):
            raise TaskOrchestratorError(
                f"cannot record capability use from {task.status.value}"
            )
        await self._repository.record_capability_use(
            project_id=project_id,
            user_id=user_id,
            task_id=task_id,
            capability=capability,
        )

    async def weak_accept(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        covered_goals: Iterable[str],
        artifacts: Sequence[ArtifactSubmission],
    ) -> WeakAcceptResult:
        """主持弱验收：对照目标、必需产物与已记录外部能力；成功则原子落库"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.REVIEWING:
            raise TaskOrchestratorError(
                f"cannot weak-accept from {task.status.value}"
            )
        used_capabilities = await self._repository.list_capability_uses(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        result = evaluate_weak_accept(
            goals=task.goals,
            covered_goals=covered_goals,
            required_artifacts=task.required_artifacts,
            artifacts=artifacts,
            granted_external=task.external_auth,
            used_capabilities=used_capabilities,
        )
        if not result.passed:
            return result
        try:
            await self._repository.complete_weak_accept(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=task.revision,
                artifacts=artifacts,
            )
        except WorkshopTaskConflictError as exc:
            raise TaskOrchestratorError(f"task conflict: {task_id}") from exc
        return result

    async def user_reject_done(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """用户驳回已完成任务，回到 re_aligning"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.DONE:
            raise TaskOrchestratorError(f"cannot reject done from {task.status.value}")
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.RE_ALIGNING,
        )

    async def fail_review_blocked(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> WorkshopTaskRecord:
        """无人值守弱验收失败，进入 blocked 等待用户回项目置顶"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status is not WorkshopTaskStatus.REVIEWING:
            raise TaskOrchestratorError(
                f"cannot fail-review from {task.status.value}"
            )
        return await self._transition(
            project_id=project_id,
            user_id=user_id,
            task=task,
            target=WorkshopTaskStatus.BLOCKED,
        )

    async def attribute_message(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: Optional[str],
        ambiguous: bool,
        candidate_task_ids: Sequence[str] = (),
    ) -> MessageAttribution:
        """主持判定消息归属；不确定必须澄清，禁止静默落到闲聊"""
        if ambiguous:
            return MessageAttribution(task_id=None, needs_clarification=True)
        if task_id is None:
            if candidate_task_ids:
                return MessageAttribution(task_id=None, needs_clarification=True)
            return MessageAttribution(task_id=None, needs_clarification=False)
        await self.get(project_id=project_id, user_id=user_id, task_id=task_id)
        return MessageAttribution(task_id=task_id, needs_clarification=False)

    async def grant_external_auth(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        capabilities: Iterable[WorkshopToolCapability],
    ) -> WorkshopTaskRecord:
        """按任务授予外部副作用能力；拒绝非外部能力"""
        requested = frozenset(capabilities)
        if any(not is_external_capability(capability) for capability in requested):
            raise TaskOrchestratorError("non-external capability cannot be granted")
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.status not in (
            WorkshopTaskStatus.AUTHORIZED,
            WorkshopTaskStatus.EXECUTING,
            WorkshopTaskStatus.BLOCKED,
        ):
            raise TaskOrchestratorError(
                f"cannot grant external auth from {task.status.value}"
            )
        merged = frozenset(task.external_auth) | requested
        try:
            return await self._repository.update_task_external_auth_cas(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=task.revision,
                expected_status=task.status,
                external_auth=merged,
            )
        except WorkshopTaskConflictError as exc:
            raise TaskOrchestratorError(f"task conflict: {task_id}") from exc

    async def has_external_auth(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        capability: WorkshopToolCapability,
    ) -> bool:
        """查询任务是否已获某项外部授权"""
        task = await self.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        return capability in task.external_auth

    async def authorize_operation(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        capability: WorkshopToolCapability,
        operation_kind: str,
        payload_hash: str,
    ):
        """记录参数级 AuthorizedOperation；要求任务已有对应外部能力 grant"""
        from datetime import datetime, timezone
        from uuid import uuid4

        from app.server.workshop.domain.ecommerce.authorized_operations import (
            AuthorizedOperation,
        )

        if not is_external_capability(capability):
            raise TaskOrchestratorError("authorized operation requires external capability")
        if not payload_hash.strip() or not operation_kind.strip():
            raise TaskOrchestratorError("operation_kind and payload_hash required")
        task = await self.get(project_id=project_id, user_id=user_id, task_id=task_id)
        if capability not in task.external_auth:
            raise TaskOrchestratorError("task external grant missing for capability")
        if task.status not in (
            WorkshopTaskStatus.AUTHORIZED,
            WorkshopTaskStatus.EXECUTING,
            WorkshopTaskStatus.BLOCKED,
        ):
            raise TaskOrchestratorError(
                f"cannot authorize operation from {task.status.value}"
            )
        operation = AuthorizedOperation(
            id=f"aop_{uuid4().hex}",
            task_id=task_id,
            capability=capability,
            operation_kind=operation_kind.strip(),
            payload_hash=payload_hash.strip(),
            granted_by=user_id,
            granted_at=datetime.now(timezone.utc),
        )
        await self._repository.append_authorized_operation_for_project(
            project_id=project_id,
            user_id=user_id,
            operation=operation,
        )
        return operation

    async def _transition(
        self,
        *,
        project_id: str,
        user_id: int,
        task: WorkshopTaskRecord,
        target: WorkshopTaskStatus,
    ) -> WorkshopTaskRecord:
        """按当前 revision 与状态 CAS 迁移任务"""
        try:
            return await self._repository.update_task_status_cas(
                project_id=project_id,
                user_id=user_id,
                task_id=task.id,
                expected_revision=task.revision,
                expected_status=task.status,
                target_status=target,
            )
        except WorkshopTaskConflictError as exc:
            raise TaskOrchestratorError(f"task conflict: {task.id}") from exc
