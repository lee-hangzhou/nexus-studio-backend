from __future__ import annotations

from app.canvas.services.canvas_service import canvas_service
from app.canvas.services.workflow_dispatch import dispatch_node_terminal
from app.canvas.turn.generation_hub import canvas_generation_hub
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.domain.canvas.enums import CanvasNodeStatus
from app.domain.enums import GatewayTaskStatus
from app.models.canvas_nodes import CanvasNodes
from app.models.generate_task import GenerateTask
from app.services.generation_assets import ensure_result_assets

TERMINAL_TASK_STATUSES = {
    GatewayTaskStatus.SUCCEEDED,
    GatewayTaskStatus.FAILED,
    GatewayTaskStatus.CANCELLED,
}


def _node_status_from_task(status: int) -> CanvasNodeStatus:
    """网关任务状态映射为画布节点状态"""
    if status in {
        GatewayTaskStatus.CREATED,
        GatewayTaskStatus.QUEUED,
        GatewayTaskStatus.WAITING,
        GatewayTaskStatus.RUNNING,
    }:
        return CanvasNodeStatus.RUNNING
    if status == GatewayTaskStatus.SUCCEEDED:
        return CanvasNodeStatus.SUCCESS
    if status in {GatewayTaskStatus.FAILED, GatewayTaskStatus.CANCELLED}:
        return CanvasNodeStatus.FAILED
    return CanvasNodeStatus.IDLE


def canvas_node_needs_sync(node: CanvasNodes, task: GenerateTask) -> bool:
    """判断 canvas_nodes 是否落后于 generate_task"""
    expected = _node_status_from_task(int(task.status))
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


async def reconcile_canvas_node_for_task(
    task: GenerateTask,
    *,
    ensure_assets: bool = True,
) -> CanvasPatchResponse | None:
    """终态任务兜底 reconcile, 已同步成功时仍触发下游推进"""
    if int(task.status) not in TERMINAL_TASK_STATUSES:
        return None
    node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    if node is None:
        return None
    if ensure_assets and int(task.status) == GatewayTaskStatus.SUCCEEDED:
        if not (isinstance(task.result_asset_ids, list) and task.result_asset_ids):
            task = await ensure_result_assets(task)
    if not canvas_node_needs_sync(node, task):
        if int(task.status) == GatewayTaskStatus.SUCCEEDED:
            await dispatch_node_terminal(
                int(node.project_id),
                str(node.id),
                user_id=int(task.user_id),
                status=CanvasNodeStatus.SUCCESS,
            )
        return None
    return await sync_canvas_node_from_generate_task(task)


async def sync_canvas_node_from_generate_task(
    task: GenerateTask,
) -> CanvasPatchResponse | None:
    """把 generate_task 状态写回 canvas node 并 SSE 广播"""
    node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    if node is None:
        return None

    status = _node_status_from_task(int(task.status))
    output_asset_ids = (
        [int(item) for item in task.result_asset_ids]
        if isinstance(task.result_asset_ids, list)
        else None
    )

    rev, node_view = await canvas_service.update_node_generation(
        int(node.project_id),
        str(node.id),
        task_id=task.id,
        status=status,
        output_asset_ids=output_asset_ids,
        error_message=task.error_message,
    )
    await canvas_service.refresh_node_asset_urls([node_view])
    payload = CanvasPatchResponse(revision=rev, nodes=[node_view])
    progress = GenerationProgress(
        node_id=node.id,
        task_id=task.id,
        status=status,
        revision=rev,
    )
    await canvas_generation_hub.publish(
        int(node.project_id),
        {
            "canvas_patch": payload.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
        },
    )
    if status in {CanvasNodeStatus.SUCCESS, CanvasNodeStatus.FAILED}:
        # 成功推进下游, 失败让下游保持等待或不提交
        await dispatch_node_terminal(
            int(node.project_id),
            str(node.id),
            user_id=int(task.user_id),
            status=status,
        )
    return payload
