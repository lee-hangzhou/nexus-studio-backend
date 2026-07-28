from __future__ import annotations

from uuid import UUID

from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.canvas.workflow.inputs import resolve_node_inputs
from app.agent.canvas.workflow.params import resolve_node_generation_config
from app.agent.runtime.ports import get_canvas_port, get_generation_port
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import CanvasNodeKind, CanvasNodeStatus
from app.server.canvas.domain.models import ResolvedCanvasInputs
from app.server.canvas.domain.node_data import (
    data_generate_error,
    data_ratio,
    data_resolution,
    data_status,
    data_task_id,
    parse_node_data,
)
from app.server.exceptions.base import AppError
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.schemas import SubmitGenerateRequest
from app.server.infra.logger import logger

RUNNABLE_NODE_KINDS = {CanvasNodeKind.IMAGE, CanvasNodeKind.VIDEO}
ADVANCEABLE_STATUSES = {
    CanvasNodeStatus.IDLE,
    CanvasNodeStatus.WAITING_INPUTS,
    CanvasNodeStatus.FAILED,
}
NON_REPEATABLE_STATUSES = {
    CanvasNodeStatus.RUNNING,
    CanvasNodeStatus.SUCCESS,
    CanvasNodeStatus.CANCELLED,
}


class CanvasWorkflowRunner:
    """根据依赖边推进下游节点生成"""

    async def advance_from_node(
        self,
        project_id: int,
        episode_id: int,
        source_node_id: str,
        *,
        user_id: int,
    ) -> None:
        """源节点完成后尝试推进全部下游"""
        target_ids = await get_canvas_port().list_dependency_targets(episode_id, source_node_id)
        for target_id in target_ids:
            await self.advance_node(project_id, episode_id, target_id, user_id=user_id)

    async def advance_node(self, project_id: int, episode_id: int, node_id: str, *, user_id: int) -> None:
        """尝试推进单节点, 输入未齐时写 waiting_inputs"""
        node = await get_canvas_port().get_node(episode_id, node_id)
        if node is None or node.kind not in RUNNABLE_NODE_KINDS:
            return
        node_data = parse_node_data(node.data)
        node_status = data_status(node_data)
        node_task_id = data_task_id(node_data)
        if node_status in NON_REPEATABLE_STATUSES or node_task_id is not None:
            return
        if node_status not in ADVANCEABLE_STATUSES:
            return

        model_id, duration_sec, config_changed = await resolve_node_generation_config(node)
        if config_changed:
            await self._persist_generation_config(
                project_id,
                episode_id,
                node_id,
                model_id=model_id,
                duration_sec=duration_sec,
            )
            node = await get_canvas_port().get_node(episode_id, node_id)
            if node is None:
                return

        resolved = await resolve_node_inputs(episode_id, node_id)
        missing = self._missing_requirements(
            resolved,
            model_id=model_id,
            duration_sec=duration_sec,
            kind=node.kind,
        )
        if missing:
            await self._update_node_state(
                project_id,
                episode_id,
                node_id,
                status=CanvasNodeStatus.WAITING_INPUTS,
                task_id=node_task_id,
                error_message="; ".join(missing),
            )
            return

        claimed = await get_canvas_port().claim_workflow_node(
            episode_id,
            node_id,
            allowed_statuses=tuple(ADVANCEABLE_STATUSES),
        )
        if claimed is None:
            return
        rev, node_view = claimed
        await get_canvas_port().publish_episode_graph_event(
            episode_id,
            canvas_patch=CanvasPatchResponse(
                nodes=[node_view],
                edges=[],
                deleted_node_ids=[],
                deleted_edge_ids=[],
            ),
            progress=GenerationProgress(
                node_id=UUID(node_id),
                task_id=None,
                status=CanvasNodeStatus.RUNNING,
                revision=rev,
            ),
        )
        try:
            prepared = await prepare_node_submit(
                episode_id,
                node_id,
                mode="agent",
                prompt=resolved.local_prompt,
                ref_asset_ids=[slot.asset_id for slot in resolved.refs],
            )
            req = SubmitGenerateRequest(
                kind=GenerationKind(node.kind.value),
                prompt=prepared.prompt,
                model_id=model_id,
                ratio=data_ratio(parse_node_data(node.data)),
                resolution=data_resolution(parse_node_data(node.data)),
                duration=duration_sec if node.kind == CanvasNodeKind.VIDEO else None,
                ref_asset_ids=list(prepared.ref_asset_ids),
            )
            submitted = await get_generation_port().submit(user_id, req)
            await self._persist_generation_config(
                project_id,
                episode_id,
                node_id,
                model_id=model_id,
                duration_sec=duration_sec if node.kind == CanvasNodeKind.VIDEO else None,
                status=CanvasNodeStatus.RUNNING,
                task_id=submitted.task_id,
                error_message="",
            )
            logger.info(
                "canvas.workflow.submitted",
                project_id=project_id,
                episode_id=episode_id,
                node_id=node_id,
                task_id=submitted.task_id,
                kind=node.kind.value,
                model_id=model_id,
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            await self._update_node_state(
                project_id,
                episode_id,
                node_id,
                status=CanvasNodeStatus.FAILED,
                task_id=None,
                error_message=message,
            )
            logger.warning(
                "canvas.workflow.submit_failed",
                project_id=project_id,
                episode_id=episode_id,
                node_id=node_id,
                error=message,
            )

    async def _persist_generation_config(
        self,
        project_id: int,
        episode_id: int,
        node_id: str,
        *,
        model_id: str | None = None,
        duration_sec: int | None = None,
        status: CanvasNodeStatus | None = None,
        task_id: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """持久化节点生成配置并经 Port 广播"""
        del project_id
        row = await get_canvas_port().get_node(episode_id, node_id)
        if row is None:
            return
        row_data = parse_node_data(row.data)
        rev, node_view = await get_canvas_port().update_node_generation(
            episode_id,
            node_id,
            task_id=data_task_id(row_data) if task_id is None else task_id,
            status=status if status is not None else data_status(row_data),
            model_id=model_id,
            duration_sec=duration_sec,
            error_message=(
                data_generate_error(row_data) if error_message is None else error_message
            ),
        )
        resolved_status = status if status is not None else (
            node_view.data.status or CanvasNodeStatus.IDLE
        )
        await get_canvas_port().publish_episode_graph_event(
            episode_id,
            canvas_patch=CanvasPatchResponse(
                nodes=[node_view],
                edges=[],
                deleted_node_ids=[],
                deleted_edge_ids=[],
            ),
            progress=GenerationProgress(
                node_id=UUID(node_id),
                task_id=task_id,
                status=resolved_status,
                revision=rev,
            ),
        )

    def _missing_requirements(
        self,
        resolved: ResolvedCanvasInputs,
        *,
        model_id: str,
        kind: CanvasNodeKind,
        duration_sec: int | None,
    ) -> list[str]:
        """整理不可运行原因"""
        missing: list[str] = []
        if not model_id:
            missing.append("model_id_not_ready")
        if not resolved.local_prompt.strip():
            missing.append("prompt_not_ready")
        if kind == CanvasNodeKind.VIDEO and duration_sec is None:
            missing.append("duration_not_ready")
        missing.extend(resolved.missing_reasons())
        return missing

    async def _update_node_state(
        self,
        project_id: int,
        episode_id: int,
        node_id: str,
        *,
        status: CanvasNodeStatus,
        task_id: int | None,
        error_message: str | None,
    ) -> None:
        """更新节点状态并经 Port 推送"""
        del project_id
        rev, node_view = await get_canvas_port().update_node_generation(
            episode_id,
            node_id,
            task_id=task_id,
            status=status,
            error_message=error_message,
        )
        await get_canvas_port().publish_episode_graph_event(
            episode_id,
            canvas_patch=CanvasPatchResponse(
                nodes=[node_view],
                edges=[],
                deleted_node_ids=[],
                deleted_edge_ids=[],
            ),
            progress=GenerationProgress(
                node_id=UUID(node_id),
                task_id=task_id,
                status=status,
                revision=rev,
            ),
        )


canvas_workflow_runner = CanvasWorkflowRunner()
