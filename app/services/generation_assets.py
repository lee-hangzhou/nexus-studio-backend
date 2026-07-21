from __future__ import annotations

from typing import Any

from app.assets.service import (
    ASSET_SOURCE_CANVAS_NODE_OUTPUT,
    ASSET_SOURCE_GENERATE_RESULT,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    asset_service,
)
from app.contracts.gateway import GatewayResultItem
from app.core.logger import logger
from app.core.object_storage import object_storage
from app.models.assets import Assets
from app.models.canvas_nodes import CanvasNodes
from app.models.generate_task import GenerateTask


def to_result_urls(result_keys: list[dict[str, Any]]) -> list[GatewayResultItem]:
    """裸 TOS key 列表 → 实时加签 URL 列表（同步，presigned_get_url 无网络调用）。"""
    result: list[GatewayResultItem] = []
    for raw_item in result_keys:
        item = GatewayResultItem.model_validate(raw_item)
        try:
            result.append(
                item.model_copy(
                    update={"url": object_storage.presigned_get_url(item.url)}
                )
            )
        except Exception as exc:
            logger.warning("generate.presign_error", key=item.url, error=str(exc))
            result.append(item)
    return result


def task_result_asset_ids(task: GenerateTask) -> list[int]:
    if not isinstance(task.result_asset_ids, list):
        return []
    return [int(item) for item in task.result_asset_ids if item is not None]


def asset_task_id(row: Assets) -> int | None:
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    raw = metadata.get("task_id")
    if raw is None and row.source_type == ASSET_SOURCE_GENERATE_RESULT:
        raw = row.source_id
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def ensure_result_assets(task: GenerateTask) -> GenerateTask:
    """确保成功任务的 result_keys 已落成 Assets，并返回刷新后的 task。"""
    if not task.result_keys or not isinstance(task.result_keys, list):
        return task
    if isinstance(task.result_asset_ids, list) and task.result_asset_ids:
        return task

    if task.kind == "video":
        asset_type = ASSET_TYPE_VIDEO
        mime_type = "video/mp4"
    elif task.kind == "audio":
        asset_type = ASSET_TYPE_AUDIO
        mime_type = "audio/mpeg"
    else:
        asset_type = ASSET_TYPE_IMAGE
        mime_type = "image/png"
    canvas_node = await CanvasNodes.filter(task_id=task.id, deleted_at__isnull=True).first()
    source_type = ASSET_SOURCE_CANVAS_NODE_OUTPUT if canvas_node is not None else ASSET_SOURCE_GENERATE_RESULT
    source_id = str(canvas_node.id) if canvas_node is not None else str(task.id)
    project_id = int(canvas_node.project_id) if canvas_node is not None else None
    asset_ids: list[int] = []
    for index, raw_item in enumerate(task.result_keys):
        item = GatewayResultItem.model_validate(raw_item)
        storage_key = item.url
        filename = storage_key.rsplit("/", 1)[-1] or f"{task.kind}-{task.id}-{index}"
        asset = await asset_service.create_asset(
            user_id=int(task.user_id),
            storage_key=storage_key,
            filename=filename,
            mime_type=mime_type,
            asset_type=asset_type,
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
            metadata={
                "task_id": task.id,
                "union_task_id": task.union_task_id,
                "kind": task.kind,
                "prompt": task.prompt,
                "model_id": task.model_id,
                "ratio": task.ratio,
                "resolution": task.resolution,
                "duration": task.duration,
                "index": index,
                "gateway_result": item.model_dump(mode="json", exclude_none=False),
                "canvas_node_id": str(canvas_node.id) if canvas_node is not None else None,
                "canvas_project_id": project_id,
            },
        )
        asset_ids.append(int(asset.id))
    if asset_ids:
        await GenerateTask.filter(id=task.id).update(result_asset_ids=asset_ids)
        task = await GenerateTask.get(id=task.id)
    return task
