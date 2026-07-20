from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.canvas.errors import GENERATION_FAILED, NODE_GENERATION_IN_PROGRESS
from app.canvas.node_submit.prepare import prepare_node_submit
from app.canvas.node_execution.generation_guard import (
    NodeGenerationInProgressError,
    claim_node_for_generation,
    node_generation_in_progress_detail,
)
from app.canvas.services.canvas_service import canvas_service
from app.chat.tools.result import ToolResult
from app.domain.canvas.enums import CanvasNodeStatus
from app.domain.generation.enums import GenerationKind, ReferenceMode
from app.models.canvas_nodes import CanvasNodes
from app.schemas.generate import SubmitGenerateRequest
from app.services.generation_submit import submit_generate_task
from app.services.generation_voices import resolve_tts_voice_id


class SubmitNodeGenerationInput(BaseModel):
    """submit_node_generation 工具入参 schema"""

    node_id: str
    kind: GenerationKind
    prompt: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    voice_id: str | None = None
    ratio: str | None = None
    resolution: str | None = None
    count: int = Field(default=1, ge=1, le=6)
    duration: int | None = Field(default=None, ge=3, le=15)
    reference_mode: ReferenceMode | None = None
    ref_attachment_ids: list[int] = Field(default_factory=list)
    ref_asset_ids: list[int] = Field(default_factory=list)
    expected_revision: int | None = Field(
        default=None,
        description="可选 revision CAS；提交生成并更新节点时用于防止旧状态覆盖",
    )


async def submit_node_generation_for_project(
    project_id: int,
    user_id: int,
    args: SubmitNodeGenerationInput,
) -> tuple[ToolResult, dict[str, Any] | None]:
    """提交生成任务, 节点标为 running, 返回 ToolResult 与可选 SSE delta"""
    try:
        rev, _claimed_view = await claim_node_for_generation(project_id, args.node_id)
        voice_id = args.voice_id
        if args.kind == GenerationKind.AUDIO:
            node_row = await CanvasNodes.filter(
                id=UUID(args.node_id),
                project_id=project_id,
                deleted_at__isnull=True,
            ).first()
            voice_id = await resolve_tts_voice_id(
                args.model_id,
                voice_id=args.voice_id,
                fallback_voice_id=node_row.voice_id if node_row is not None else None,
            )
        prepared = await prepare_node_submit(
            project_id,
            args.node_id,
            mode="agent",
            prompt=args.prompt,
            ref_asset_ids=args.ref_asset_ids,
            ref_attachment_ids=args.ref_attachment_ids,
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
            ref_attachment_ids=list(prepared.ref_attachment_ids),
            ref_asset_ids=list(prepared.ref_asset_ids),
        )
        try:
            submitted = await submit_generate_task(user_id, req)
        except Exception as exc:
            await canvas_service.update_node_generation(
                project_id,
                args.node_id,
                task_id=None,
                status=CanvasNodeStatus.FAILED,
                error_message=str(exc),
                expected_revision=rev,
            )
            raise
        rev, node_view = await canvas_service.update_node_generation(
            project_id,
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
        delta = {
            "revision": rev,
            "nodes": [node_view.model_dump(mode="json")],
            "edges": [],
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
    except Exception as exc:
        return ToolResult.fail(GENERATION_FAILED, detail=str(exc)), None


def build_submit_node_generation_tool(project_id: int, user_id: int) -> StructuredTool:
    """构建 submit_node_generation 结构化工具"""
    async def _run(**kwargs: Any) -> str:
        """工具入口, 校验参数后提交节点生成"""
        args = SubmitNodeGenerationInput.model_validate(kwargs)
        result, _ = await submit_node_generation_for_project(project_id, user_id, args)
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
