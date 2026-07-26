from __future__ import annotations

from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.agent.canvas.workflow.inputs import resolve_node_inputs
from app.agent.canvas.workflow.params import resolve_node_generation_config
from app.agent.runtime.ports import get_canvas_port, get_generation_port
from app.server.canvas.domain.enums import CanvasNodeKind, CanvasNodeStatus
from app.server.canvas.domain.models import ResolvedCanvasInputs
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
        """源节点完成后, 尝试推进其全部下游节点"""
        target_ids = await get_canvas_port().list_dependency_targets(episode_id, source_node_id)
        for target_id in target_ids:
            await self.advance_node(project_id, episode_id, target_id, user_id=user_id)

    async def advance_node(self, project_id: int, episode_id: int, node_id: str, *, user_id: int) -> None:
        """尝试推进单节点, 输入未齐时写 waiting_inputs"""
        node = await get_canvas_port().get_node(episode_id, node_id)
        if node is None or node.kind not in RUNNABLE_NODE_KINDS:
            return
        if node.status in NON_REPEATABLE_STATUSES or node.task_id is not None:
            # 已运行或已有任务的节点不重复提交, callback 重放幂等
            return
        if node.status not in ADVANCEABLE_STATUSES:
            return

        model_id, duration_sec, config_changed = await resolve_node_generation_config(node)
        if config_changed:
            # 系统默认补齐 model 或时长后先持久化并通知前端
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
            # 输入未齐不标失败, 记录等待原因待上游完成后再推
            await self._update_node_state(
                project_id,
                episode_id,
                node_id,
                status=CanvasNodeStatus.WAITING_INPUTS,
                task_id=node.task_id,
                error_message="; ".join(missing),
            )
            return

        claimed = await get_canvas_port().claim_workflow_node(
            episode_id,
            node_id,
            allowed_statuses=tuple(ADVANCEABLE_STATUSES),
        )
        if claimed is None:
            # 多 worker 或重复回调并发时仅一个 claim 成功
            return
        rev, node_view = claimed
        await canvas_generation_hub.publish(
            episode_id,
            {
                "canvas_patch": {
                    "revision": rev,
                    "nodes": [node_view.model_dump(mode="json")],
                    "edges": [],
                    "deleted_node_ids": [],
                    "deleted_edge_ids": [],
                },
                "progress": {
                    "node_id": node_id,
                    "task_id": None,
                    "status": CanvasNodeStatus.RUNNING.value,
                    "revision": rev,
                },
            },
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
                ratio=node.ratio,
                resolution=node.resolution,
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
        """持久化节点生成配置并通过 hub 广播"""
        row = await get_canvas_port().get_node(episode_id, node_id)
        if row is None:
            return
        rev, node_view = await get_canvas_port().update_node_generation(
            episode_id,
            node_id,
            task_id=row.task_id if task_id is None else task_id,
            status=status if status is not None else row.status,
            model_id=model_id,
            duration_sec=duration_sec,
            error_message=error_message if error_message is not None else row.error_message,
        )
        payload = {
            "revision": rev,
            "nodes": [node_view.model_dump(mode="json")],
            "edges": [],
            "deleted_node_ids": [],
            "deleted_edge_ids": [],
        }
        await canvas_generation_hub.publish(
            episode_id,
            {
                "canvas_patch": payload,
                "progress": {
                    "node_id": node_id,
                    "task_id": task_id,
                    "status": (status if status is not None else CanvasNodeStatus(node_view.status)).value,
                    "revision": rev,
                },
            },
        )

    def _missing_requirements(
        self,
        resolved: ResolvedCanvasInputs,
        *,
        model_id: str,
        kind: CanvasNodeKind,
        duration_sec: int | None,
    ) -> list[str]:
        """整理不可运行原因为结构化字符串列表"""
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
        """更新节点状态并通过 generation hub 推送前端"""
        rev, node_view = await get_canvas_port().update_node_generation(
            episode_id,
            node_id,
            task_id=task_id,
            status=status,
            error_message=error_message,
        )
        payload = {
            "revision": rev,
            "nodes": [node_view.model_dump(mode="json")],
            "edges": [],
            "deleted_node_ids": [],
            "deleted_edge_ids": [],
        }
        await canvas_generation_hub.publish(
            episode_id,
            {
                "canvas_patch": payload,
                "progress": {
                    "node_id": node_id,
                    "task_id": task_id,
                    "status": status.value,
                    "revision": rev,
                },
            },
        )


canvas_workflow_runner = CanvasWorkflowRunner()
