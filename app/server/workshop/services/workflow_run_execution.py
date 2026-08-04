"""工作流运行执行：读运行记录、跑 DAG、回写终态。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.server.workshop.domain.enums import WorkshopWorkflowRunStatus
from app.server.workshop.domain.preset_catalog import workshop_preset_catalog
from app.server.workshop.domain.workflow_executor import (
    NodeRunner,
    ProducedArtifact,
    execute_workflow_graph,
)
from app.server.workshop.persistence.repository import WorkshopRepository, utc_now


class WorkflowRunExecutionService:
    """消费端执行服务"""

    def __init__(
        self,
        *,
        repository: WorkshopRepository,
        runner: NodeRunner,
        clock=utc_now,
    ) -> None:
        """注入仓储与节点执行器"""
        self._repository = repository
        self._runner = runner
        self._clock = clock

    async def execute_run(
        self, *, project_id: str, user_id: int, run_id: str
    ) -> WorkshopWorkflowRunStatus:
        """执行一条运行记录"""
        run = await self._repository.get_workflow_run(
            project_id=project_id, user_id=user_id, run_id=run_id
        )
        if run.status not in {
            WorkshopWorkflowRunStatus.QUEUED,
            WorkshopWorkflowRunStatus.RUNNING,
        }:
            return run.status
        workflow = await self._repository.get_workflow(
            project_id=project_id,
            user_id=user_id,
            workflow_id=run.workflow_id,
        )
        started = self._clock()
        run = await self._repository.update_workflow_run_cas(
            project_id=project_id,
            user_id=user_id,
            run_id=run_id,
            expected_revision=run.revision,
            status=WorkshopWorkflowRunStatus.RUNNING,
            current_node_id=None,
            error_message=None,
            started_at=started,
            finished_at=None,
        )
        keys, allowlists = workshop_preset_catalog()
        state = await execute_workflow_graph(
            run_id=run_id,
            project_id=project_id,
            nodes=workflow.nodes,
            edges=workflow.edges,
            model_key=workflow.model_key,
            known_preset_keys=keys,
            preset_allowlists=allowlists,
            runner=self._runner,
        )
        finished = self._clock()
        await self._repository.update_workflow_run_cas(
            project_id=project_id,
            user_id=user_id,
            run_id=run_id,
            expected_revision=run.revision,
            status=state.status,
            current_node_id=state.current_node_id,
            error_message=state.error_message,
            started_at=started,
            finished_at=finished,
        )
        return state.status


class UnwiredExpertNodeRunner:
    """专家 runtime 未接线时 fail closed"""

    async def run_node(self, ctx) -> list[ProducedArtifact]:
        """拒绝静默伪造产物"""
        raise RuntimeError(
            "expert node runner not wired; refuse placeholder artifacts"
        )
