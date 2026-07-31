from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional, Sequence, Tuple
from uuid import uuid4

from app.server.workshop.domain.cron_next_fire import (
    InvalidCronExpressionError,
    InvalidTimezoneError,
    compute_next_run_at,
)
from app.server.workshop.domain.enums import (
    WorkshopTaskStatus,
    WorkshopToolCapability,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.domain.external_tool_gate import is_external_capability
from app.server.workshop.domain.types import (
    ArtifactSubmission,
    WorkflowStep,
    WorkshopEventRecord,
    WorkshopScheduleRecord,
    WorkshopScheduleRunRecord,
    WorkshopTaskRecord,
    WorkshopWorkflowRecord,
)
from app.server.workshop.domain.weak_accept import evaluate_weak_accept
from app.server.workshop.persistence.repository import (
    WorkshopRepository,
    WorkshopScheduleNotFoundError,
    WorkshopTaskConflictError,
    WorkshopWorkflowConflictError,
    WorkshopWorkflowNotFoundError,
    new_id,
    utc_now,
)
from app.server.workshop.services.task_orchestrator import WorkshopTaskOrchestrator


class WorkshopWorkflowScheduleError(Exception):
    """非法工作流/定时操作（fail closed）"""


@dataclass(frozen=True, slots=True)
class ManualRunResult:
    """手动跑工作流：一次轻量确认后进入 authorized"""

    task: WorkshopTaskRecord
    used_light_confirmation: bool


@dataclass(frozen=True, slots=True)
class ScheduleTriggerResult:
    """定时触发结果"""

    task: WorkshopTaskRecord
    run: WorkshopScheduleRunRecord
    event_ids: Tuple[str, ...]
    requires_external_auth_popup: bool


@dataclass(frozen=True, slots=True)
class ScheduledCompletionResult:
    """定时运行完成/失败结果"""

    task_status: WorkshopTaskStatus
    event_ids: Tuple[str, ...]


def _normalize_steps(steps: Sequence[WorkflowStep]) -> tuple[WorkflowStep, ...]:
    """校验并规范化工作流步骤"""
    normalized: list[WorkflowStep] = []
    for step in steps:
        title = step.title.strip()
        if not title:
            raise WorkshopWorkflowScheduleError("workflow step title required")
        required = tuple(name.strip() for name in step.required_artifact_names)
        if any(not name for name in required):
            raise WorkshopWorkflowScheduleError("required artifact name required")
        externals = tuple(step.external_capabilities)
        if any(not is_external_capability(capability) for capability in externals):
            raise WorkshopWorkflowScheduleError(
                "workflow step external_capabilities must be external"
            )
        normalized.append(
            WorkflowStep(
                title=title,
                required_artifact_names=required,
                external_capabilities=externals,
            )
        )
    return tuple(normalized)


def _external_capabilities_from_steps(
    steps: Sequence[WorkflowStep],
) -> frozenset[WorkshopToolCapability]:
    """汇总工作流步骤声明的外部能力"""
    caps: set[WorkshopToolCapability] = set()
    for step in steps:
        caps.update(step.external_capabilities)
    return frozenset(caps)


MANUAL_TRIGGER_KEY_PREFIX = "manual:"


def _namespace_manual_trigger_key(trigger_key: str) -> str:
    """将调用方 trigger_key 命名空间化为 manual:<key>，避免与到期确定性键碰撞"""
    key = trigger_key.strip()
    if not key:
        raise WorkshopWorkflowScheduleError("trigger_key required")
    if key.startswith(MANUAL_TRIGGER_KEY_PREFIX):
        raise WorkshopWorkflowScheduleError(
            "trigger_key must not include manual: prefix; service namespaces it"
        )
    return f"{MANUAL_TRIGGER_KEY_PREFIX}{key}"


def _to_trigger_result(
    task: WorkshopTaskRecord,
    event: WorkshopEventRecord,
    run: WorkshopScheduleRunRecord,
) -> ScheduleTriggerResult:
    """组装触发结果"""
    return ScheduleTriggerResult(
        task=task,
        run=run,
        event_ids=(event.event_key,),
        requires_external_auth_popup=False,
    )


class WorkshopWorkflowScheduleService:
    """工坊工作流与定时应用服务"""

    def __init__(
        self,
        *,
        repository: WorkshopRepository,
        orchestrator: WorkshopTaskOrchestrator,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        """注入仓储、任务编排器与可选时钟"""
        self._repository = repository
        self._orchestrator = orchestrator
        self._clock = clock or utc_now

    async def agent_draft_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        steps: Sequence[WorkflowStep],
    ) -> WorkshopWorkflowRecord:
        """Agent 编排工作流草稿（未确认不可跑）"""
        return await self._new_draft(
            project_id=project_id,
            user_id=user_id,
            name=name,
            steps=steps,
            source=WorkshopWorkflowSource.AGENT,
        )

    async def user_draft_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        steps: Sequence[WorkflowStep],
    ) -> WorkshopWorkflowRecord:
        """用户编排工作流草稿"""
        return await self._new_draft(
            project_id=project_id,
            user_id=user_id,
            name=name,
            steps=steps,
            source=WorkshopWorkflowSource.USER,
        )

    async def user_confirm_save_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> WorkshopWorkflowRecord:
        """用户确认保存工作流定义"""
        workflow = await self._require_workflow(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        if workflow.status is not WorkshopWorkflowStatus.DRAFT:
            raise WorkshopWorkflowScheduleError("only drafts can be confirmed")
        try:
            return await self._repository.save_workflow_cas(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
                expected_revision=workflow.revision,
            )
        except WorkshopWorkflowConflictError as exc:
            raise WorkshopWorkflowScheduleError("workflow confirm conflict") from exc

    async def list_saved_workflows(
        self, *, project_id: str, user_id: int
    ) -> Tuple[WorkshopWorkflowRecord, ...]:
        """列出已保存工作流"""
        rows = await self._repository.list_saved_workflows(
            project_id=project_id, user_id=user_id
        )
        return tuple(rows)

    async def manual_run_with_light_confirm(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        authorized_capabilities: Sequence[WorkshopToolCapability] = (),
    ) -> ManualRunResult:
        """手动跑已保存工作流：校验外部授权范围后原子创建 executing 任务"""
        workflow = await self._require_saved(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        authorized = frozenset(authorized_capabilities)
        if any(not is_external_capability(capability) for capability in authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must be external"
            )
        required = _external_capabilities_from_steps(workflow.steps)
        if not required.issubset(authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must cover workflow external scope"
            )
        task = await self._repository.create_executing_task_from_workflow(
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow_id,
            task_id=str(uuid4()),
            external_auth=authorized,
        )
        return ManualRunResult(task=task, used_light_confirmation=True)

    async def create_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        cron: str,
        timezone: str,
        authorized_capabilities: Sequence[WorkshopToolCapability],
        now: Optional[datetime] = None,
    ) -> WorkshopScheduleRecord:
        """创建定时；授权集合必须覆盖工作流声明的全部外部能力并写入 next_run_at"""
        workflow = await self._require_saved(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        cron_text = cron.strip()
        timezone_name = timezone.strip()
        if not cron_text:
            raise WorkshopWorkflowScheduleError("cron required")
        if not timezone_name:
            raise WorkshopWorkflowScheduleError("timezone required")
        authorized = frozenset(authorized_capabilities)
        if any(not is_external_capability(capability) for capability in authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must be external"
            )
        required = _external_capabilities_from_steps(workflow.steps)
        if not required.issubset(authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must cover workflow external scope"
            )
        authorized_at = now if now is not None else self._clock()
        if authorized_at.tzinfo is None:
            raise WorkshopWorkflowScheduleError("now must be timezone-aware")
        try:
            next_run_at = compute_next_run_at(
                cron=cron_text,
                timezone_name=timezone_name,
                after=authorized_at,
            )
        except (InvalidCronExpressionError, InvalidTimezoneError) as exc:
            raise WorkshopWorkflowScheduleError(str(exc)) from exc
        return await self._repository.create_schedule(
            schedule_id=new_id("sched"),
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow_id,
            cron=cron_text,
            timezone_name=timezone_name,
            authorized_at=authorized_at,
            authorized_external_capabilities=authorized,
            next_run_at=next_run_at,
        )

    async def trigger_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
        trigger_key: str,
    ) -> ScheduleTriggerResult:
        """手动 run-now 触发：命名空间化 trigger_key，不推进 next_run_at；同键幂等"""
        namespaced = _namespace_manual_trigger_key(trigger_key)
        try:
            task, event, run = await self._repository.trigger_schedule_run(
                project_id=project_id,
                user_id=user_id,
                schedule_id=schedule_id,
                trigger_key=namespaced,
                task_id=str(uuid4()),
                started_event_key=new_id("evt"),
            )
        except WorkshopScheduleNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown schedule: {schedule_id}"
            ) from exc
        return _to_trigger_result(task, event, run)

    async def tick_once(
        self,
        *,
        now: datetime,
        batch_size: int,
    ) -> Tuple[ScheduleTriggerResult, ...]:
        """处理一批到期定时；CAS 失败跳过且不计入成功"""
        if now.tzinfo is None:
            raise WorkshopWorkflowScheduleError("now must be timezone-aware")
        if batch_size < 1:
            raise WorkshopWorkflowScheduleError("batch_size must be >= 1")
        candidates = await self._repository.list_due_schedules(
            now=now, limit=batch_size
        )
        claimed: list[ScheduleTriggerResult] = []
        for schedule in candidates:
            if schedule.next_run_at is None:
                continue
            bundle = await self._repository.claim_due_schedule_run(
                schedule_id=schedule.id,
                expected_next_run_at=schedule.next_run_at,
                task_id=str(uuid4()),
                started_event_key=new_id("evt"),
            )
            if bundle is None:
                continue
            task, event, run = bundle
            claimed.append(_to_trigger_result(task, event, run))
        return tuple(claimed)

    async def complete_scheduled_run(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        covered_goals: Iterable[str],
        artifacts: Sequence[ArtifactSubmission],
    ) -> ScheduledCompletionResult:
        """定时运行弱验收：仅接受 schedule 授权任务；成功/失败均走仓储原子写"""
        task = await self._orchestrator.get(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task.schedule_id is None or not task.schedule_authorized:
            raise WorkshopWorkflowScheduleError(
                "complete_scheduled_run requires schedule_id and schedule_authorized"
            )
        if task.status is WorkshopTaskStatus.EXECUTING:
            task = await self._orchestrator.mark_reviewing(
                project_id=project_id, user_id=user_id, task_id=task_id
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
        if result.passed:
            try:
                _, summary, published = await self._repository.complete_scheduled_success(
                    project_id=project_id,
                    user_id=user_id,
                    task_id=task_id,
                    expected_revision=task.revision,
                    artifacts=artifacts,
                    summary_event_key=new_id("evt"),
                    published_event_key=new_id("evt"),
                )
            except WorkshopTaskConflictError as exc:
                raise WorkshopWorkflowScheduleError(
                    f"task conflict: {task_id}"
                ) from exc
            return ScheduledCompletionResult(
                task_status=WorkshopTaskStatus.DONE,
                event_ids=(summary.event_key, published.event_key),
            )
        try:
            _, blocked = await self._repository.complete_scheduled_blocked(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=task.revision,
                reasons=result.reasons,
                blocked_event_key=new_id("evt"),
            )
        except WorkshopTaskConflictError as exc:
            raise WorkshopWorkflowScheduleError(f"task conflict: {task_id}") from exc
        return ScheduledCompletionResult(
            task_status=WorkshopTaskStatus.BLOCKED,
            event_ids=(blocked.event_key,),
        )

    async def get_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
    ) -> WorkshopScheduleRecord:
        """按所有权读取定时"""
        try:
            return await self._repository.get_schedule(
                project_id=project_id, user_id=user_id, schedule_id=schedule_id
            )
        except WorkshopScheduleNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown schedule: {schedule_id}"
            ) from exc

    async def list_schedules(
        self, *, project_id: str, user_id: int
    ) -> Tuple[WorkshopScheduleRecord, ...]:
        """按所有权列出定时"""
        rows = await self._repository.list_schedules(
            project_id=project_id, user_id=user_id
        )
        return tuple(rows)

    async def disable_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
    ) -> WorkshopScheduleRecord:
        """禁用定时：不可 claim，并清空 next_run_at"""
        try:
            return await self._repository.disable_schedule(
                project_id=project_id, user_id=user_id, schedule_id=schedule_id
            )
        except WorkshopScheduleNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown schedule: {schedule_id}"
            ) from exc

    async def enable_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
        now: Optional[datetime] = None,
    ) -> WorkshopScheduleRecord:
        """启用定时：校验 cron/tz，在行锁下写入新的 next_run_at"""
        try:
            schedule = await self._repository.get_schedule(
                project_id=project_id, user_id=user_id, schedule_id=schedule_id
            )
        except WorkshopScheduleNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown schedule: {schedule_id}"
            ) from exc
        moment = now if now is not None else self._clock()
        if moment.tzinfo is None:
            raise WorkshopWorkflowScheduleError("now must be timezone-aware")
        try:
            next_run_at = compute_next_run_at(
                cron=schedule.cron,
                timezone_name=schedule.timezone,
                after=moment,
            )
        except (InvalidCronExpressionError, InvalidTimezoneError) as exc:
            raise WorkshopWorkflowScheduleError(str(exc)) from exc
        try:
            return await self._repository.enable_schedule(
                project_id=project_id,
                user_id=user_id,
                schedule_id=schedule_id,
                next_run_at=next_run_at,
            )
        except WorkshopScheduleNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown schedule: {schedule_id}"
            ) from exc

    async def list_events(
        self, *, project_id: str, user_id: int
    ) -> Tuple[WorkshopEventRecord, ...]:
        """列出项目事件"""
        rows = await self._repository.list_events(
            project_id=project_id, user_id=user_id
        )
        return tuple(rows)

    async def _new_draft(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        steps: Sequence[WorkflowStep],
        source: WorkshopWorkflowSource,
    ) -> WorkshopWorkflowRecord:
        """创建工作流草稿"""
        normalized = _normalize_steps(steps)
        if not name.strip() or not normalized:
            raise WorkshopWorkflowScheduleError("name and steps required")
        return await self._repository.create_workflow(
            workflow_id=new_id("wf"),
            project_id=project_id,
            user_id=user_id,
            name=name.strip(),
            steps=normalized,
            status=WorkshopWorkflowStatus.DRAFT,
            source=source,
        )

    async def _require_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> WorkshopWorkflowRecord:
        """读取工作流；校验归属"""
        try:
            return await self._repository.get_workflow(
                project_id=project_id, user_id=user_id, workflow_id=workflow_id
            )
        except WorkshopWorkflowNotFoundError as exc:
            raise WorkshopWorkflowScheduleError(
                f"unknown workflow: {workflow_id}"
            ) from exc

    async def _require_saved(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> WorkshopWorkflowRecord:
        """读取已保存工作流"""
        workflow = await self._require_workflow(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        if workflow.status is not WorkshopWorkflowStatus.SAVED:
            raise WorkshopWorkflowScheduleError("workflow is not saved")
        return workflow
