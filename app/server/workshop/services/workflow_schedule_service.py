"""工坊工作流定义、手动/定时触发与运行记录应用服务。"""

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
    WorkshopWorkflowRunStatus,
    WorkshopWorkflowRunTrigger,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.domain.external_tool_gate import is_external_capability
from app.server.workshop.domain.preset_catalog import workshop_preset_catalog
from app.server.workshop.domain.types import (
    ArtifactSubmission,
    WorkshopEventRecord,
    WorkshopScheduleRecord,
    WorkshopScheduleRunRecord,
    WorkshopTaskRecord,
    WorkshopWorkflowRecord,
    WorkshopWorkflowRunRecord,
)
from app.server.workshop.domain.weak_accept import evaluate_weak_accept
from app.server.workshop.domain.workflow_definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowEdge,
    WorkflowNode,
    validate_workflow_definition,
)
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
    """手动跑工作流：创建运行记录并入队"""

    run: WorkshopWorkflowRunRecord


@dataclass(frozen=True, slots=True)
class ScheduleTriggerResult:
    """定时触发结果（旧 schedule_runs 路径，过渡期保留）"""

    task: WorkshopTaskRecord
    run: WorkshopScheduleRunRecord
    event_ids: Tuple[str, ...]
    requires_external_auth_popup: bool
    workflow_run: WorkshopWorkflowRunRecord | None = None


@dataclass(frozen=True, slots=True)
class ScheduledCompletionResult:
    """定时运行完成/失败结果"""

    task_status: WorkshopTaskStatus
    event_ids: Tuple[str, ...]


def _external_capabilities_from_nodes(
    nodes: Sequence[WorkflowNode],
) -> frozenset[WorkshopToolCapability]:
    """汇总节点声明且需弹窗授权的外部能力"""
    caps: set[WorkshopToolCapability] = set()
    for node in nodes:
        for capability in node.external_capabilities:
            if is_external_capability(capability):
                caps.add(capability)
    return frozenset(caps)


def _validate_definition_graph(
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
    entry_node_ids: Sequence[str] | None = None,
) -> None:
    """用目录校验 DAG 定义"""
    keys, allowlists = workshop_preset_catalog()
    try:
        validate_workflow_definition(
            WorkflowDefinition(
                nodes=tuple(nodes),
                edges=tuple(edges),
                known_preset_keys=keys,
                preset_allowlists=allowlists,
                entry_node_ids=tuple(entry_node_ids) if entry_node_ids else None,
            )
        )
    except WorkflowDefinitionError as exc:
        raise WorkshopWorkflowScheduleError(str(exc)) from exc


MANUAL_TRIGGER_KEY_PREFIX = "manual:"


def _namespace_manual_trigger_key(trigger_key: str) -> str:
    """将调用方 trigger_key 命名空间化为 manual:<key>"""
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
    workflow_run: WorkshopWorkflowRunRecord | None = None,
) -> ScheduleTriggerResult:
    """组装触发结果"""
    return ScheduleTriggerResult(
        task=task,
        run=run,
        event_ids=(event.event_key,),
        requires_external_auth_popup=False,
        workflow_run=workflow_run,
    )


class WorkshopWorkflowScheduleService:
    """工坊工作流与定时应用服务"""

    def __init__(
        self,
        *,
        repository: WorkshopRepository,
        orchestrator: WorkshopTaskOrchestrator,
        clock: Optional[Callable[[], datetime]] = None,
        enqueue_run: Optional[Callable[[str, int, str], None]] = None,
    ) -> None:
        """注入仓储、编排器、时钟与入队回调"""
        self._repository = repository
        self._orchestrator = orchestrator
        self._clock = clock or utc_now
        self._enqueue_run = enqueue_run

    async def agent_draft_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        nodes: Sequence[WorkflowNode],
        edges: Sequence[WorkflowEdge],
        model_key: str,
        entry_node_ids: Sequence[str] | None = None,
    ) -> WorkshopWorkflowRecord:
        """Agent 编排工作流草稿"""
        return await self._new_draft(
            project_id=project_id,
            user_id=user_id,
            name=name,
            nodes=nodes,
            edges=edges,
            model_key=model_key,
            entry_node_ids=entry_node_ids,
            source=WorkshopWorkflowSource.AGENT,
        )

    async def user_draft_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        nodes: Sequence[WorkflowNode],
        edges: Sequence[WorkflowEdge],
        model_key: str,
        entry_node_ids: Sequence[str] | None = None,
    ) -> WorkshopWorkflowRecord:
        """用户编排工作流草稿"""
        return await self._new_draft(
            project_id=project_id,
            user_id=user_id,
            name=name,
            nodes=nodes,
            edges=edges,
            model_key=model_key,
            entry_node_ids=entry_node_ids,
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

    async def list_workflow_runs(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str | None = None,
        limit: int = 50,
    ) -> Tuple[WorkshopWorkflowRunRecord, ...]:
        """列出运行记录"""
        rows = await self._repository.list_workflow_runs(
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow_id,
            limit=limit,
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
        """手动跑已保存工作流：校验授权后创建运行记录并入队"""
        workflow = await self._require_saved(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        authorized = frozenset(authorized_capabilities)
        if any(not is_external_capability(capability) for capability in authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must be external"
            )
        required = _external_capabilities_from_nodes(workflow.nodes)
        if not required.issubset(authorized):
            raise WorkshopWorkflowScheduleError(
                "authorized capabilities must cover workflow external scope"
            )
        run = await self._repository.create_workflow_run(
            run_id=str(uuid4()),
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_revision=workflow.revision,
            trigger=WorkshopWorkflowRunTrigger.MANUAL,
            status=WorkshopWorkflowRunStatus.QUEUED,
        )
        await self._repository.create_executing_task_from_workflow(
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow_id,
            task_id=run.id,
            external_auth=authorized,
        )
        self._enqueue(project_id=project_id, user_id=user_id, run_id=run.id)
        return ManualRunResult(run=run)

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
        """创建定时；授权集合必须覆盖工作流声明的全部外部能力"""
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
        required = _external_capabilities_from_nodes(workflow.nodes)
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
        """手动触发定时：建旧 task 痕迹并建 workflow_run 入队"""
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
        schedule = await self._repository.get_schedule(
            project_id=project_id, user_id=user_id, schedule_id=schedule_id
        )
        workflow = await self._require_saved(
            project_id=project_id,
            user_id=user_id,
            workflow_id=schedule.workflow_id,
        )
        workflow_run = await self._repository.create_workflow_run(
            run_id=str(uuid4()),
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow.id,
            workflow_revision=workflow.revision,
            trigger=WorkshopWorkflowRunTrigger.SCHEDULE,
            schedule_id=schedule_id,
            status=WorkshopWorkflowRunStatus.QUEUED,
        )
        # 复用 schedule 触发创建的 executing task：将其 id 与 run 对齐不便；另建同 id 任务
        # claim 已建 task — 把授权复制到 run.id 任务壳
        await self._repository.create_executing_task_from_workflow(
            project_id=project_id,
            user_id=user_id,
            workflow_id=workflow.id,
            task_id=workflow_run.id,
            external_auth=schedule.authorized_external_capabilities,
        )
        self._enqueue(project_id=project_id, user_id=user_id, run_id=workflow_run.id)
        return _to_trigger_result(task, event, run, workflow_run)

    async def tick_once(
        self,
        *,
        now: datetime,
        batch_size: int,
    ) -> Tuple[ScheduleTriggerResult, ...]:
        """处理一批到期定时并入队 workflow_run"""
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
            user_id = await self._repository.get_project_owner_user_id(
                project_id=schedule.project_id
            )
            workflow = await self._require_saved(
                project_id=schedule.project_id,
                user_id=user_id,
                workflow_id=schedule.workflow_id,
            )
            workflow_run = await self._repository.create_workflow_run(
                run_id=str(uuid4()),
                project_id=schedule.project_id,
                user_id=user_id,
                workflow_id=workflow.id,
                workflow_revision=workflow.revision,
                trigger=WorkshopWorkflowRunTrigger.SCHEDULE,
                schedule_id=schedule.id,
                status=WorkshopWorkflowRunStatus.QUEUED,
            )
            await self._repository.create_executing_task_from_workflow(
                project_id=schedule.project_id,
                user_id=user_id,
                workflow_id=workflow.id,
                task_id=workflow_run.id,
                external_auth=schedule.authorized_external_capabilities,
            )
            self._enqueue(
                project_id=schedule.project_id, user_id=user_id, run_id=workflow_run.id
            )
            claimed.append(_to_trigger_result(task, event, run, workflow_run))
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
        """定时运行弱验收（旧 task 路径）"""
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
        """禁用定时"""
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
        """启用定时"""
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

    def _enqueue(self, *, project_id: str, user_id: int, run_id: str) -> None:
        """入队执行；未配置回调则跳过"""
        if self._enqueue_run is None:
            return
        self._enqueue_run(project_id, user_id, run_id)

    async def _new_draft(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        nodes: Sequence[WorkflowNode],
        edges: Sequence[WorkflowEdge],
        model_key: str,
        source: WorkshopWorkflowSource,
        entry_node_ids: Sequence[str] | None = None,
    ) -> WorkshopWorkflowRecord:
        """创建工作流草稿"""
        if not name.strip() or not nodes:
            raise WorkshopWorkflowScheduleError("name and nodes required")
        key = model_key.strip()
        if not key:
            raise WorkshopWorkflowScheduleError("model_key required")
        _validate_definition_graph(nodes, edges, entry_node_ids)
        return await self._repository.create_workflow(
            workflow_id=new_id("wf"),
            project_id=project_id,
            user_id=user_id,
            name=name.strip(),
            nodes=nodes,
            edges=edges,
            model_key=key,
            entry_node_ids=entry_node_ids,
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
