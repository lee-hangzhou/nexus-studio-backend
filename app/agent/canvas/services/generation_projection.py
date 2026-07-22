from __future__ import annotations

from app.agent.canvas.services.workflow_dispatch import dispatch_node_terminal
from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.services import generation_sync
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.generation.domain.gateway_status import TERMINAL_GATEWAY_TASK_STATUSES, GatewayTaskStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.generation.persistence.generate_task import GenerateTask


async def project_from_task(task: GenerateTask) -> CanvasPatchResponse | None:
    """投影 generate_task → canvas，并推送 SSE / 触发终态下游"""
    status = GatewayTaskStatus(task.status)
    node_before = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()

    if status in TERMINAL_GATEWAY_TASK_STATUSES:
        payload = await generation_sync.reconcile_canvas_node_for_task(task)
        if payload is None and node_before is not None:
            # 已与 generate_task 对齐时仍需推进下游 workflow
            if status == GatewayTaskStatus.SUCCEEDED:
                await dispatch_node_terminal(
                    node_before.project_id,
                    str(node_before.id),
                    user_id=task.user_id,
                    status=CanvasNodeStatus.SUCCESS,
                )
            return None
        if payload is not None:
            await publish_canvas_node_result(task, payload)
        return payload

    payload = await generation_sync.sync_canvas_node_from_generate_task(task)
    if payload is not None:
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
    node_status = generation_sync.node_status_from_task(task.status)
    progress = GenerationProgress(
        node_id=node.id,
        task_id=task.id,
        status=node_status,
        revision=payload.revision,
    )
    await canvas_generation_hub.publish(
        node.project_id,
        {
            "canvas_patch": payload.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
        },
    )
    if node_status in {CanvasNodeStatus.SUCCESS, CanvasNodeStatus.FAILED}:
        await dispatch_node_terminal(
            node.project_id,
            str(node.id),
            user_id=task.user_id,
            status=node_status,
        )
