from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool

from app.agent.canvas.errors import GENERATION_FAILED, NODE_GENERATION_IN_PROGRESS
from app.agent.canvas.node_execution.generation_guard import (
    node_generation_in_progress_detail,
)
from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port, get_generation_port
from app.contracts.canvas import CanvasNodeData, CanvasNodeView, CanvasPosition
from app.server.canvas.schemas.generation import SubmitNodeGenerationInput
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.schemas import SubmitGenerateRequest
from app.server.ports.product import CanvasNodeDTO


def _node_view_from_dto(dto: CanvasNodeDTO) -> CanvasNodeView:
    """将 Port 节点 DTO 转为契约 CanvasNodeView"""
    return CanvasNodeView(
        id=UUID(dto.id),
        kind=dto.kind,
        revision=dto.revision,
        position=CanvasPosition(x=dto.position_x, y=dto.position_y),
        width=dto.width,
        height=dto.height,
        data=CanvasNodeData.model_validate(dto.data),
        output_asset_urls=list(dto.output_asset_urls) or None,
    )


async def submit_node_generation_for_episode(
    project_id: int,
    episode_id: int,
    user_id: int,
    args: SubmitNodeGenerationInput,
) -> tuple[ToolResult, dict[str, Any] | None]:
    """Agent 提交节点生成，prepare 后走 GenerationService.submit 绑定节点"""
    del project_id
    async with canvas_episode_fence.generation(episode_id):
        return await _submit_node_generation_locked(episode_id, user_id, args)


async def _submit_node_generation_locked(
    episode_id: int,
    user_id: int,
    args: SubmitNodeGenerationInput,
) -> tuple[ToolResult, dict[str, Any] | None]:
    """已持生成栅栏的 Agent 提交实现"""
    canvas = get_canvas_port()
    try:
        voice_id = args.voice_id
        if args.kind == GenerationKind.AUDIO:
            node_row = await canvas.get_node(episode_id, args.node_id)
            from app.server.canvas.domain.node_data import data_voice_id, parse_node_data

            fallback = (
                data_voice_id(parse_node_data(node_row.data)) if node_row is not None else None
            )
            voice_id = await get_generation_port().resolve_tts_voice_id(
                args.model_id,
                voice_id=args.voice_id,
                fallback_voice_id=fallback,
            )
        prepared = await prepare_node_submit(
            episode_id,
            args.node_id,
            mode="agent",
            prompt=args.prompt,
            ref_asset_ids=args.ref_asset_ids,
        )
        req = SubmitGenerateRequest(
            kind=args.kind,
            prompt=prepared.prompt,
            model_id=args.model_id,
            voice_id=voice_id,
            ratio=args.ratio,
            resolution=args.resolution,
            count=args.count,
            duration=args.duration,
            reference_mode=args.reference_mode,
            ref_asset_ids=list(prepared.ref_asset_ids),
            episode_id=episode_id,
            node_id=args.node_id,
        )
        submitted = await get_generation_port().submit(user_id, req)
        node_dto = await canvas.get_node(episode_id, args.node_id)
        if node_dto is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {args.node_id} not found")
        node_view = _node_view_from_dto(node_dto)
        delta = {
            "nodes": [node_view.model_dump(mode="json")],
            "edges": [],
            "deleted_node_ids": [],
            "deleted_edge_ids": [],
        }
        out = {
            "task_id": submitted.task_id,
            "status": submitted.status,
            "revision": node_view.revision,
            "node_id": args.node_id,
        }
        return ToolResult.ok(json.dumps(out, ensure_ascii=False)), delta
    except AppError as exc:
        if exc.code == ErrorCode.CANVAS_NODE_GENERATION_IN_PROGRESS:
            task_id = None
            if exc.details and exc.details.get("task_id") is not None:
                task_id = int(exc.details["task_id"])
            return (
                ToolResult.fail(
                    NODE_GENERATION_IN_PROGRESS,
                    detail=node_generation_in_progress_detail(task_id=task_id),
                ),
                None,
            )
        return ToolResult.fail(GENERATION_FAILED, detail=exc.message), None
    except Exception as exc:
        return ToolResult.fail(GENERATION_FAILED, detail=str(exc) or "generation failed"), None


def build_submit_node_generation_tool(*, project_id: int, episode_id: int, user_id: int) -> StructuredTool:
    """构建 submit_node_generation 结构化工具"""

    async def _run(**kwargs: Any) -> str:
        """工具入口, 校验参数后提交节点生成"""
        args = SubmitNodeGenerationInput.model_validate(kwargs)
        result, _ = await submit_node_generation_for_episode(project_id, episode_id, user_id, args)
        return result.to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="submit_node_generation",
        description=(
            "REQUIRED this turn: call read_canvas_skill(name=\"canvas_generation\") before this tool. "
            "Listing/querying alone does not waive the read. "
            "Start image/video/audio generation. Required: node_id (UUID from query or patch), "
            "kind (image|video|audio), prompt, model_id from list_generate_models. "
            "Call list_generate_models first; pick ratio/resolution/duration/count/reference_mode "
            "from that model's param_options. "
            "Video requires reference_mode and duration. Audio may need voice_id. "
            "In manual mode, confirm-card edits to prompt/config win. "
            "If the node already has a generation in progress, returns node_generation_in_progress — "
            "do not retry or create a replacement node. "
            "Progress via generation_progress SSE."
        ),
        args_schema=SubmitNodeGenerationInput,
    )
