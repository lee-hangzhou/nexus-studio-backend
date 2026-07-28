from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool

from app.agent.canvas.errors import GENERATION_FAILED, NODE_GENERATION_IN_PROGRESS
from app.agent.canvas.node_execution.generation_guard import (
    NodeGenerationInProgressError,
    claim_node_for_generation,
    node_generation_in_progress_detail,
)
from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port, get_generation_port
from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.canvas.schemas.generation import SubmitNodeGenerationInput
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.exceptions.base import AppError
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.schemas import SubmitGenerateRequest


async def submit_node_generation_for_episode(
    project_id: int,
    episode_id: int,
    user_id: int,
    args: SubmitNodeGenerationInput,
) -> tuple[ToolResult, dict[str, Any] | None]:
    """提交生成任务, 节点标为 running, 返回 ToolResult 与可选 SSE delta"""
    async with canvas_episode_fence.generation(episode_id):
        return await _submit_node_generation_locked(project_id, episode_id, user_id, args)


async def _submit_node_generation_locked(
    project_id: int,
    episode_id: int,
    user_id: int,
    args: SubmitNodeGenerationInput,
) -> tuple[ToolResult, dict[str, Any] | None]:
    """已持生成栅栏的提交实现"""
    canvas = get_canvas_port()
    try:
        rev, _claimed_view = await claim_node_for_generation(episode_id, args.node_id)
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
        )
        try:
            submitted = await get_generation_port().submit(user_id, req)
        except Exception as exc:
            error_message = exc.message if isinstance(exc, AppError) else "generation failed"
            fail_rev, fail_view = await canvas.update_node_generation(
                episode_id,
                args.node_id,
                task_id=None,
                status=CanvasNodeStatus.FAILED,
                error_message=error_message,
                expected_revision=rev,
            )
            await canvas.publish_episode_graph_event(
                episode_id,
                canvas_patch=CanvasPatchResponse(
                    nodes=[fail_view],
                    edges=[],
                    deleted_node_ids=[],
                    deleted_edge_ids=[],
                ),
                progress=GenerationProgress(
                    node_id=UUID(args.node_id),
                    task_id=None,
                    status=CanvasNodeStatus.FAILED,
                    revision=fail_rev,
                ),
            )
            raise
        rev, node_view = await canvas.update_node_generation(
            episode_id,
            args.node_id,
            task_id=submitted.task_id,
            status=CanvasNodeStatus.RUNNING,
            model_id=args.model_id,
            voice_id=voice_id,
            duration_sec=args.duration if args.kind == GenerationKind.VIDEO else None,
            ratio=args.ratio,
            resolution=args.resolution,
            expected_revision=rev,
        )
        await canvas.publish_episode_graph_event(
            episode_id,
            canvas_patch=CanvasPatchResponse(
                nodes=[node_view],
                edges=[],
                deleted_node_ids=[],
                deleted_edge_ids=[],
            ),
            progress=GenerationProgress(
                node_id=UUID(args.node_id),
                task_id=submitted.task_id,
                status=CanvasNodeStatus.RUNNING,
                revision=rev,
            ),
        )
        delta = {
            "nodes": [node_view.model_dump(mode="json")],
            "edges": [],
            "deleted_node_ids": [],
            "deleted_edge_ids": [],
        }
        out = {
            "task_id": submitted.task_id,
            "status": submitted.status,
            "revision": rev,
            "node_id": args.node_id,
        }
        return ToolResult.ok(json.dumps(out, ensure_ascii=False)), delta
    except NodeGenerationInProgressError as exc:
        task_id = exc.details.get("task_id") if exc.details else None
        return (
            ToolResult.fail(
                NODE_GENERATION_IN_PROGRESS,
                detail=node_generation_in_progress_detail(
                    task_id=int(task_id) if task_id is not None else None
                ),
            ),
            None,
        )
    except Exception:
        return ToolResult.fail(GENERATION_FAILED, detail="generation failed"), None


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
            "Start image/video/audio generation. Required: node_id (UUID from query or patch), "
            "kind (image|video|audio), prompt, model_id. "
            "For audio/TTS also pass voice_id when known. "
            "If the node already has a generation in progress, returns node_generation_in_progress — "
            "do not retry or create a replacement node. "
            'Example: {"node_id":"<uuid>","kind":"video","prompt":"...","model_id":"..."}. '
            "Progress via generation_progress SSE."
        ),
        args_schema=SubmitNodeGenerationInput,
    )
