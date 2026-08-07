from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from tortoise.transactions import in_transaction

from app.contracts.canvas import CanvasNodeData, CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.canvas.domain.node_data import data_has_output_assets, dump_node_data, parse_node_data
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.services.canvas_service import node_view_from_row
from app.server.canvas.services.episode_events import publish_patch_and_progress
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.services import canvas_scope_service

NodeGenerateGate = Literal["idle", "busy", "done"]


async def classify_node_generate_gate(*, episode_id: int, node_id: str) -> NodeGenerateGate:
    """任务权威门闩：busy=非终态占用；done=已有成功产出；idle=可提交"""
    row = await CanvasNodes.filter(
        id=UUID(node_id),
        episode_id=episode_id,
        deleted_at__isnull=True,
    ).first()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
    data = parse_node_data(row.data)
    task_id = data.generate_task_id
    if task_id is not None:
        task = await GenerateTask.filter(id=task_id, deleted_at__isnull=True).first()
        if task is not None and GatewayTaskStatus(task.status).is_non_terminal:
            return "busy"
        if task is not None and GatewayTaskStatus(task.status) == GatewayTaskStatus.SUCCEEDED:
            return "done"
        if task is not None and GatewayTaskStatus(task.status) in {
            GatewayTaskStatus.FAILED,
            GatewayTaskStatus.CANCELLED,
        }:
            return "idle"
    if data_has_output_assets(data):
        return "done"
    return "idle"


async def assert_node_has_no_active_generate_task(
    *,
    episode_id: int,
    node_id: str,
    user_id: int,
) -> None:
    """节点若已绑定非终态任务则拒绝再提交"""
    await canvas_scope_service.require_write_scope(user_id, episode_id)
    gate = await classify_node_generate_gate(episode_id=episode_id, node_id=node_id)
    if gate != "busy":
        return
    row = await CanvasNodes.filter(
        id=UUID(node_id),
        episode_id=episode_id,
        deleted_at__isnull=True,
    ).first()
    task_id = parse_node_data(row.data).generate_task_id if row is not None else None
    raise AppError(
        ErrorCode.CANVAS_NODE_GENERATION_IN_PROGRESS,
        "该节点已有生成任务进行中。无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。",
        details={
            "error_type": "node_generation_in_progress",
            "node_id": node_id,
            "task_id": task_id,
        },
    )


async def bind_generate_task_to_node(
    *,
    episode_id: int,
    node_id: str,
    user_id: int,
    task_id: int,
) -> None:
    """任务入队成功后写入节点 generate_task_id，并发布画布补丁"""
    await canvas_scope_service.require_write_scope(user_id, episode_id)
    async with in_transaction():
        episode = await ProjectEpisodes.select_for_update().filter(
            id=episode_id,
            deleted_at__isnull=True,
        ).first()
        if episode is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
        meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
        if meta is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
        row = await CanvasNodes.select_for_update().filter(
            id=UUID(node_id),
            episode_id=episode_id,
            deleted_at__isnull=True,
        ).first()
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
        existing = parse_node_data(row.data)
        if existing.generate_task_id is not None and existing.generate_task_id != task_id:
            prior = await GenerateTask.filter(
                id=existing.generate_task_id,
                deleted_at__isnull=True,
            ).first()
            if prior is not None and GatewayTaskStatus(prior.status).is_non_terminal:
                raise AppError(
                    ErrorCode.CANVAS_NODE_GENERATION_IN_PROGRESS,
                    "该节点已有生成任务进行中。无需重复提交，等待该任务完成即可。不要重试，不要新建替代节点。",
                    details={
                        "error_type": "node_generation_in_progress",
                        "node_id": node_id,
                        "task_id": existing.generate_task_id,
                    },
                )
        payload = existing.model_dump(mode="python")
        payload["generate_task_id"] = task_id
        row.data = dump_node_data(CanvasNodeData.model_validate(payload))
        row.revision = row.revision + 1
        await row.save()
        episode.updated_at = datetime.now(timezone.utc)
        await episode.save(update_fields=["updated_at"])
        view = node_view_from_row(row)
        rev = row.revision
    await publish_patch_and_progress(
        episode_id,
        canvas_patch=CanvasPatchResponse(
            nodes=[view],
            edges=[],
            deleted_node_ids=[],
            deleted_edge_ids=[],
        ),
        progress=GenerationProgress(
            node_id=UUID(node_id),
            task_id=task_id,
            status=CanvasNodeStatus.RUNNING,
            revision=rev,
        ),
    )
