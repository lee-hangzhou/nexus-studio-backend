from __future__ import annotations

from app.agent.canvas.workflow.runner import canvas_workflow_runner
from app.server.canvas.domain.enums import CanvasNodeStatus


async def dispatch_node_terminal(
    project_id: int,
    episode_id: int,
    node_id: str,
    *,
    user_id: int,
    status: CanvasNodeStatus,
) -> None:
    """节点进入终态后推进下游, 失败节点由 runner 保持等待或不提交"""
    if status in {CanvasNodeStatus.SUCCESS, CanvasNodeStatus.FAILED}:
        await canvas_workflow_runner.advance_from_node(
            project_id,
            episode_id,
            node_id,
            user_id=user_id,
        )
