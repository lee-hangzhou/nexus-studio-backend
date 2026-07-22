from __future__ import annotations

from uuid import UUID

from tortoise.transactions import in_transaction

from app.canvas.errors import NODE_GENERATION_IN_PROGRESS
from app.canvas.services.canvas_service import node_view_from_row
from app.contracts.canvas import CanvasNodeView
from app.core.logger import logger
from app.domain.canvas.enums import CanvasNodeStatus
from app.domain.enums import GatewayTaskStatus
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.canvas_nodes import CanvasNodes
from app.models.canvas_project_meta import CanvasProjectMeta
from app.models.generate_task import GenerateTask


def node_generation_in_progress_detail(*, task_id: int | None = None) -> str:
    """给模型与用户统一的占用说明文案。"""
    if task_id is not None:
        return (
            f"该节点已有生成任务进行中(task_id={task_id})。"
            "无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。"
        )
    return "该节点已有生成任务进行中。无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。"


class NodeGenerationInProgressError(AppError):
    """节点已有在途生成，拒绝重复提交。"""

    def __init__(self, *, node_id: str, task_id: int | None = None) -> None:
        super().__init__(
            ErrorCode.CANVAS_NODE_GENERATION_IN_PROGRESS,
            "该节点已有生成任务进行中",
            details={
                "error_type": NODE_GENERATION_IN_PROGRESS,
                "node_id": node_id,
                "task_id": task_id,
            },
        )


async def _active_task_id_for_node(row: CanvasNodes) -> int | None:
    """若节点关联非终态 generate_task，返回 task_id。"""
    if row.task_id is None:
        return None
    task = await GenerateTask.filter(id=row.task_id, deleted_at__isnull=True).first()
    if task is None:
        return None
    if GatewayTaskStatus(task.status).is_non_terminal:
        return int(task.id)
    return None


async def assert_node_generation_idle(row: CanvasNodes) -> None:
    """在已持有 node 行锁的前提下检查是否在途。"""
    if row.status == CanvasNodeStatus.RUNNING.value:
        active_task_id = await _active_task_id_for_node(row)
        logger.info(
            "canvas.node_generation.rejected",
            action="in_progress",
            project_id=row.project_id,
            node_id=str(row.id),
            active_task_id=active_task_id,
            reason="node_running",
        )
        raise NodeGenerationInProgressError(node_id=str(row.id), task_id=active_task_id)

    active_task_id = await _active_task_id_for_node(row)
    if active_task_id is not None:
        logger.info(
            "canvas.node_generation.rejected",
            action="in_progress",
            project_id=row.project_id,
            node_id=str(row.id),
            active_task_id=active_task_id,
            reason="task_non_terminal",
        )
        raise NodeGenerationInProgressError(node_id=str(row.id), task_id=active_task_id)


async def claim_node_for_generation(project_id: int, node_id: str) -> tuple[int, CanvasNodeView]:
    """占坑：同行锁检查在途 + 标 running + 推进 revision，commit 后返回。"""
    async with in_transaction():
        await CanvasProjectMeta.select_for_update().get(project_id=project_id)
        row = await CanvasNodes.select_for_update().get(
            id=UUID(node_id),
            project_id=project_id,
            deleted_at__isnull=True,
        )
        await assert_node_generation_idle(row)
        row.status = CanvasNodeStatus.RUNNING.value
        row.error_message = ""
        await row.save()
        meta = await CanvasProjectMeta.select_for_update().get(project_id=project_id)
        meta.revision = int(meta.revision) + 1
        await meta.save()
        rev = int(meta.revision)
    refreshed = await CanvasNodes.get(id=UUID(node_id))
    return rev, node_view_from_row(refreshed)
