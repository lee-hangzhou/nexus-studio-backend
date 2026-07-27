from __future__ import annotations

from uuid import UUID

from app.agent.canvas.errors import NODE_GENERATION_IN_PROGRESS
from app.agent.runtime.ports import get_canvas_port
from app.contracts.canvas import CanvasNodeView, CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.logger import logger


def node_generation_in_progress_detail(*, task_id: int | None = None) -> str:
    """给模型与用户统一的占用说明文案"""
    if task_id is not None:
        return (
            f"该节点已有生成任务进行中(task_id={task_id})。"
            "无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。"
        )
    return "该节点已有生成任务进行中。无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。"


class NodeGenerationInProgressError(AppError):
    """节点已有在途生成, 拒绝重复提交"""

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


async def claim_node_for_generation(episode_id: int, node_id: str) -> tuple[int, CanvasNodeView]:
    """经 CanvasPort 占坑并立刻推集级事件; 删集持锁时拒绝"""
    await canvas_episode_fence.assert_writable(episode_id)
    claim = await get_canvas_port().claim_node_for_generation(episode_id, node_id)
    if not claim.claimed or claim.revision is None or claim.node is None:
        logger.info(
            "canvas.node_generation.rejected",
            action="in_progress",
            episode_id=episode_id,
            node_id=node_id,
            active_task_id=claim.active_task_id,
            reason="claim_rejected",
        )
        raise NodeGenerationInProgressError(node_id=node_id, task_id=claim.active_task_id)
    await get_canvas_port().publish_episode_graph_event(
        episode_id,
        canvas_patch=CanvasPatchResponse(
            nodes=[claim.node],
            edges=[],
            deleted_node_ids=[],
            deleted_edge_ids=[],
        ),
        progress=GenerationProgress(
            node_id=UUID(node_id),
            task_id=claim.node.task_id,
            status=CanvasNodeStatus.RUNNING,
            revision=claim.revision,
        ),
    )
    return claim.revision, claim.node
