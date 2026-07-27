from __future__ import annotations

import json

from app.agent.canvas.errors import NODE_GENERATION_IN_PROGRESS
from app.agent.canvas.node_execution.text import execute_text_node_generation
from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.canvas.node_submit.types import ManualMaterialRef, MentionItemRef
from app.server.canvas.schemas.api import CanvasNodeGenerateResponse
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.agent.canvas.tools.generation import submit_node_generation_for_episode
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
    """用户手动节点生成：入口持有集级锁, 不依赖 expected_revision"""
    node_id = body.node_id
    if body.kind == "text":
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
            status=node_view.status,
            task_id=node_view.task_id,
            node=node_view,
            error_message=node_view.error_message,
        )

    if body.kind not in ("image", "video", "audio"):
        raise AppError(ErrorCode.INVALID_PARAMS, f"unsupported kind: {body.kind}")

    prepared = await prepare_node_submit(
        episode_id,
        node_id,
        mode="manual",
        prompt=body.prompt,
        ref_asset_ids=body.ref_asset_ids,
        ref_attachment_ids=body.ref_attachment_ids,
        content=body.submit_content or [],
        manual_refs=[
            ManualMaterialRef(asset_id=item.asset_id, material_id=item.material_id)
            for item in body.manual_refs
        ],
        preview_media_refs=[
            MentionItemRef(asset_id=asset_id, type="image")
            for asset_id in body.preview_media_asset_ids
        ],
    )
    gen_input = body.to_generation_input()
    gen_input = gen_input.model_copy(
        update={
            "expected_revision": None,
            "prompt": prepared.prompt,
            "ref_asset_ids": list(prepared.ref_asset_ids),
            "ref_attachment_ids": list(prepared.ref_attachment_ids),
        }
    )
    result, delta = await submit_node_generation_for_episode(project_id, episode_id, user_id, gen_input)
    if not result.success:
        if result.error_type == NODE_GENERATION_IN_PROGRESS:
            raise AppError(
                ErrorCode.CANVAS_NODE_GENERATION_IN_PROGRESS,
                result.error_detail or "该节点已有生成任务进行中",
                details={
                    "error_type": NODE_GENERATION_IN_PROGRESS,
                    "node_id": node_id,
                },
            )
        raise AppError(
            ErrorCode.GATEWAY_PROTOCOL_ERROR,
            result.error_detail or "generation failed",
            details={"error_type": result.error_type, "node_id": node_id},
        )
    if delta is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "generation returned no patch")
    node_view = CanvasNodeView.model_validate(delta["nodes"][0])
    task_id = None
    try:
        out = json.loads(result.output)
        raw_task_id = out.get("task_id")
        if raw_task_id is not None:
            task_id = int(raw_task_id)
    except (json.JSONDecodeError, TypeError, ValueError):
        task_id = node_view.task_id
    return CanvasNodeGenerateResponse(
        node_id=node_id,
        kind=body.kind,
        status=CanvasNodeStatus.RUNNING,
        task_id=task_id,
        node=node_view,
        error_message=None,
    )
