from __future__ import annotations

from app.agent.canvas.node_execution.text import execute_text_node_generation
from app.server.canvas.schemas.api import CanvasNodeGenerateResponse
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.contracts.canvas import CanvasNodeView
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


async def run_manual_node_generate(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    body: SubmitNodeExecuteInput,
) -> CanvasNodeGenerateResponse:
    """文本节点手动生成入口；媒体人手走 /generate/submit + episode/node 绑定"""
    node_id = body.node_id
    if body.kind != "text":
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "媒体节点请使用 /generate/submit（携带 episode_id 与 node_id）",
            details={"node_id": node_id, "kind": body.kind},
        )
    if not body.model_key:
        raise AppError(ErrorCode.INVALID_PARAMS, "text 节点需要 model_key")
    _, delta = await execute_text_node_generation(
        project_id=project_id,
        episode_id=episode_id,
        user_id=user_id,
        node_id=node_id,
        model_key=body.model_key,
        prompt=body.prompt,
        expected_revision=None,
    )
    node_view = CanvasNodeView.model_validate(delta["nodes"][0])
    return CanvasNodeGenerateResponse(
        node_id=node_id,
        kind="text",
        status=node_view.data.status or CanvasNodeStatus.IDLE,
        task_id=node_view.data.generate_task_id,
        node=node_view,
        error_message=node_view.data.generate_error,
    )
