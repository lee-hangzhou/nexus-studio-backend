from __future__ import annotations

from app.contracts.canvas import CanvasPatchResponse
from app.server.canvas.services.canvas_service import canvas_service
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.generation.domain.gateway_status import (
    NON_TERMINAL_GATEWAY_TASK_STATUSES,
    TERMINAL_GATEWAY_TASK_STATUSES,
    GatewayTaskStatus,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes


async def _scope_for_node(node: CanvasNodes, *, user_id: int) -> CanvasScope | None:
    episode = await ProjectEpisodes.filter(id=node.episode_id, deleted_at__isnull=True).first()
    if episode is None:
        return None
    return CanvasScope(project_id=int(episode.project_id), episode_id=int(episode.id), user_id=user_id)


def node_status_from_task(status: int) -> CanvasNodeStatus:
    """网关任务状态映射为画布节点状态"""
    gateway_status = GatewayTaskStatus(status)
    if gateway_status in NON_TERMINAL_GATEWAY_TASK_STATUSES:
        return CanvasNodeStatus.RUNNING
    if gateway_status == GatewayTaskStatus.SUCCEEDED:
        return CanvasNodeStatus.SUCCESS
    if gateway_status in {GatewayTaskStatus.FAILED, GatewayTaskStatus.CANCELLED}:
        return CanvasNodeStatus.FAILED
    raise AppError(
        ErrorCode.GENERATION_STATUS_UNAVAILABLE,
        "任务状态不符合协议",
        {"status": status},
    )


def canvas_node_needs_sync(node: CanvasNodes, task: GenerateTask) -> bool:
    """判断 canvas_nodes 是否落后于 generate_task"""
    expected = node_status_from_task(task.status)
    if node.status != expected:
        return True
    if expected == CanvasNodeStatus.SUCCESS:
        task_assets = task.result_asset_ids if isinstance(task.result_asset_ids, list) else []
        node_assets = node.output_asset_ids if isinstance(node.output_asset_ids, list) else []
        if task_assets and not node_assets:
            return True
    if expected == CanvasNodeStatus.FAILED and task.error_message and not node.error_message:
        return True
    return False


async def project_from_task(task: GenerateTask) -> CanvasPatchResponse | None:
    """由画布消费 generate_task 事实：仅投影节点（无 Agent 副作用）"""
    status = GatewayTaskStatus(task.status)
    if status in TERMINAL_GATEWAY_TASK_STATUSES:
        return await reconcile_canvas_node_for_task(task)
    return await sync_canvas_node_from_generate_task(task)


async def reconcile_canvas_node_for_task(
    task: GenerateTask,
    *,
    ensure_assets: bool = True,
) -> CanvasPatchResponse | None:
    """终态任务兜底 reconcile；已同步时返回 None（下游推进由 Agent 编排）"""
    status = GatewayTaskStatus(task.status)
    if status not in TERMINAL_GATEWAY_TASK_STATUSES:
        return None
    node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    if node is None:
        return None
    if ensure_assets and status == GatewayTaskStatus.SUCCEEDED:
        if not (isinstance(task.result_asset_ids, list) and task.result_asset_ids):
            from app.server.generation.binding import get_generation_service

            task = await get_generation_service().ensure_result_assets(task)
    if not canvas_node_needs_sync(node, task):
        return None
    return await sync_canvas_node_from_generate_task(task)


async def sync_canvas_node_from_generate_task(
    task: GenerateTask,
) -> CanvasPatchResponse | None:
    """把 generate_task 状态写回 canvas node"""
    node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    if node is None:
        return None
    scope = await _scope_for_node(node, user_id=int(task.user_id))
    if scope is None:
        return None

    status = node_status_from_task(task.status)
    output_asset_ids = (
        list(task.result_asset_ids)
        if isinstance(task.result_asset_ids, list)
        else None
    )

    _, node_view = await canvas_service.update_node_generation(
        node.episode_id,
        str(node.id),
        task_id=task.id,
        status=status,
        output_asset_ids=output_asset_ids,
        error_message=task.error_message,
        scope=scope,
    )
    return CanvasPatchResponse(nodes=[node_view])
