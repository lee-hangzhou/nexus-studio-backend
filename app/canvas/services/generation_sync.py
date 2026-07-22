from __future__ import annotations

from app.canvas.services.canvas_service import canvas_service
from app.canvas.services.workflow_dispatch import dispatch_node_terminal
from app.canvas.turn.generation_hub import canvas_generation_hub
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.domain.canvas.enums import CanvasNodeStatus
from app.domain.enums import (
    NON_TERMINAL_GATEWAY_TASK_STATUSES,
    TERMINAL_GATEWAY_TASK_STATUSES,
    GatewayTaskStatus,
)
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.canvas_nodes import CanvasNodes
from app.models.generate_task import GenerateTask
from app.services.generation_assets import ensure_result_assets


def _node_status_from_task(status: int) -> CanvasNodeStatus:
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


async def project_from_task(task: GenerateTask) -> CanvasPatchResponse | None:
    """由画布消费 generate_task 事实：投影节点并推送 SSE"""
    status = GatewayTaskStatus(task.status)
    if status in TERMINAL_GATEWAY_TASK_STATUSES:
        return await reconcile_canvas_node_for_task(task)
    return await sync_canvas_node_from_generate_task(task, publish=True)


async def reconcile_canvas_node_for_task(
    task: GenerateTask,
    *,
    ensure_assets: bool = True,
) -> CanvasPatchResponse | None:
    """终态任务兜底 reconcile, 已同步成功时仍触发下游推进"""
    if GatewayTaskStatus(task.status) not in TERMINAL_GATEWAY_TASK_STATUSES:
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
    *,
    publish: bool = True,
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
    if publish:
        await publish_canvas_node_result(task, payload)
    return payload


async def publish_canvas_node_result(
    task: GenerateTask,
    payload: CanvasPatchResponse | None,
) -> None:
    if payload is None:
        return
    node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    if node is None:
        raise AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            "生成任务关联的 Canvas 节点不存在",
            {"task_id": task.id},
        )
    status = _node_status_from_task(int(task.status))
    progress = GenerationProgress(
        node_id=node.id,
        task_id=task.id,
        status=status,
        revision=payload.revision,
    )
    await canvas_generation_hub.publish(
        int(node.project_id),
        {
            "canvas_patch": payload.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
        },
    )
    if status in {CanvasNodeStatus.SUCCESS, CanvasNodeStatus.FAILED}:
        await dispatch_node_terminal(
            int(node.project_id),
            str(node.id),
            user_id=int(task.user_id),
            status=status,
        )
