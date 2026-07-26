from __future__ import annotations

from app.agent.canvas.services.workflow_dispatch import dispatch_node_terminal
from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.agent.runtime.ports import get_canvas_port
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.ports.product import CanvasTaskProjectionDTO


async def project_from_task(task_id: int, user_id: int) -> CanvasPatchResponse | None:
    """投影生成任务，并在 Agent 编排层发布 SSE 与推进下游节点"""
    projection = await get_canvas_port().project_generation_task(task_id, user_id)
    if projection is None:
        return None
    if projection.patch is not None:
        await publish_canvas_node_result(projection)
    elif projection.status == CanvasNodeStatus.SUCCESS:
        await dispatch_node_terminal(
            projection.project_id,
            projection.episode_id,
            projection.node_id,
            user_id=projection.user_id,
            status=projection.status,
        )
    return projection.patch


async def publish_canvas_node_result(projection: CanvasTaskProjectionDTO) -> None:
    payload = projection.patch
    if payload is None:
        return
    progress = GenerationProgress(
        node_id=projection.node_id,
        task_id=projection.task_id,
        status=projection.status,
        revision=payload.revision,
    )
    await canvas_generation_hub.publish(
        projection.episode_id,
        {
            "canvas_patch": payload.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
        },
    )
    if projection.status in {CanvasNodeStatus.SUCCESS, CanvasNodeStatus.FAILED}:
        await dispatch_node_terminal(
            projection.project_id,
            projection.episode_id,
            projection.node_id,
            user_id=projection.user_id,
            status=projection.status,
        )
