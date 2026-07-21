from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from app.assets.service import asset_service
from app.chat.attachments.service import chat_attachment_service
from app.contracts.gateway import GatewayQueueItem
from app.core.gateway import gateway_client
from app.core.logger import logger
from app.domain.enums import GatewayTaskStatus
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.assets import Assets
from app.models.chat_attachments import ChatAttachments
from app.models.generate_task import GenerateTask
from app.schemas.generate import GenerateRefMaterial, GenerateTasksStatusResponse, GenerateTaskView
from app.services.generation_assets import task_result_asset_ids, to_result_urls
from app.services.generation_canvas_bridge import reconcile_canvas_for_task
from app.services.generation_result import apply_generation_result, normalize_generation_result

FallbackPoll = Callable[[GenerateTask], Awaitable[GenerateTask]]
QueueInfo = Callable[[int, int], Awaitable[Optional[GatewayQueueItem]]]
QueueInfoMap = Callable[[list[GenerateTask]], Awaitable[dict[int, GatewayQueueItem]]]
RefMaterialsMap = Callable[[list[GenerateTask]], Awaitable[dict[int, list[GenerateRefMaterial]]]]
FavoriteMap = Callable[[list[GenerateTask]], Awaitable[dict[int, bool]]]
CanvasReconcile = Callable[[GenerateTask, str], Awaitable[Any]]

TERMINAL_STATUSES: set[int] = {
    GatewayTaskStatus.SUCCEEDED,
    GatewayTaskStatus.FAILED,
    GatewayTaskStatus.CANCELLED,
}
NON_TERMINAL_STATUSES: set[int] = {
    GatewayTaskStatus.CREATED,
    GatewayTaskStatus.QUEUED,
    GatewayTaskStatus.WAITING,
    GatewayTaskStatus.RUNNING,
}


def _status_label(status: int) -> str:
    if status in (GatewayTaskStatus.CREATED, GatewayTaskStatus.QUEUED, GatewayTaskStatus.WAITING):
        return "pending"
    if status == GatewayTaskStatus.RUNNING:
        return "running"
    if status == GatewayTaskStatus.SUCCEEDED:
        return "success"
    if status in {GatewayTaskStatus.FAILED, GatewayTaskStatus.CANCELLED}:
        return "failed"
    raise AppError(
        ErrorCode.GENERATION_STATUS_UNAVAILABLE,
        "任务状态不符合协议",
        {"status": status},
    )


def _should_update(current: int, fresh: int) -> bool:
    try:
        current = int(GatewayTaskStatus(current))
        fresh = int(GatewayTaskStatus(fresh))
    except ValueError as exc:
        raise AppError(
            ErrorCode.GENERATION_STATUS_UNAVAILABLE,
            "任务状态不符合协议",
            {"current": current, "fresh": fresh},
        ) from exc
    if current in TERMINAL_STATUSES:
        return False
    if fresh in TERMINAL_STATUSES:
        return True
    return fresh >= current


def _task_to_view(
    task: GenerateTask,
    queue_info: Optional[GatewayQueueItem] = None,
    ref_materials: Optional[list[GenerateRefMaterial]] = None,
    is_favorited: Optional[bool] = None,
) -> GenerateTaskView:
    result_urls = to_result_urls(task.result_keys) if task.result_keys else []
    result_asset_ids = [int(item) for item in task.result_asset_ids] if isinstance(task.result_asset_ids, list) else []
    view = GenerateTaskView(
        task_id=task.id,
        kind=task.kind,
        status=_status_label(task.status),
        prompt=task.prompt,
        model_id=task.model_id,
        ratio=task.ratio,
        resolution=task.resolution,
        duration=task.duration,
        reference_mode=task.reference_mode,
        ref_materials=ref_materials or [],
        result_count=len(result_urls),
        result_urls=result_urls,
        result_asset_ids=result_asset_ids,
        error_message=task.error_message,
        is_favorited=bool(is_favorited) if is_favorited is not None else False,
        created_at=task.created_at,
    )
    if queue_info is not None:
        view.queue_status = queue_info.status
        view.queue_position = queue_info.position
        view.queue_total = queue_info.total
        view.estimated_wait_seconds = queue_info.estimated_wait_seconds
    return view


async def _load_ref_materials_map(
    tasks: list[GenerateTask],
) -> dict[int, list[GenerateRefMaterial]]:
    attachment_ids: set[int] = set()
    asset_ids: set[int] = set()
    for task in tasks:
        for aid in task.ref_attachment_ids or []:
            attachment_ids.add(int(aid))
        for aid in task.ref_asset_ids or []:
            asset_ids.add(int(aid))
    if not attachment_ids and not asset_ids:
        return {}

    attachment_rows = await ChatAttachments.filter(id__in=list(attachment_ids)) if attachment_ids else []
    attachment_by_id = {row.id: row for row in attachment_rows}
    asset_rows = await Assets.filter(id__in=list(asset_ids), deleted_at__isnull=True) if asset_ids else []
    asset_by_id = {row.id: row for row in asset_rows}

    result: dict[int, list[GenerateRefMaterial]] = {}
    for task in tasks:
        materials: list[GenerateRefMaterial] = []
        for aid in task.ref_attachment_ids or []:
            row = attachment_by_id.get(int(aid))
            if row is None:
                continue
            materials.append(
                GenerateRefMaterial(
                    attachment_id=row.id,
                    asset_id=row.asset_id,
                    filename=row.filename,
                    mime_type=row.mime_type,
                    url=chat_attachment_service.build_preview_url(row.storage_key),
                    source_type="chat_attachment",
                )
            )
        for aid in task.ref_asset_ids or []:
            row = asset_by_id.get(int(aid))
            if row is None:
                continue
            materials.append(
                GenerateRefMaterial(
                    asset_id=row.id,
                    filename=row.filename,
                    mime_type=row.mime_type,
                    url=asset_service.preview_url(row.storage_key),
                    source_type=row.source_type,
                )
            )
        result[task.id] = materials
    return result


async def _favorite_map_for_tasks(tasks: list[GenerateTask]) -> dict[int, bool]:
    asset_ids: set[int] = set()
    for task in tasks:
        asset_ids.update(task_result_asset_ids(task))
    if not asset_ids:
        return {int(task.id): False for task in tasks}
    rows = await Assets.filter(id__in=list(asset_ids), deleted_at__isnull=True).all()
    favorite_asset_ids = {int(row.id) for row in rows if bool(row.favorite)}
    result: dict[int, bool] = {}
    for task in tasks:
        result[int(task.id)] = any(asset_id in favorite_asset_ids for asset_id in task_result_asset_ids(task))
    return result


async def _fallback_poll(task: GenerateTask) -> GenerateTask:
    try:
        resp = await gateway_client.get_task(task.union_task_id)
        data = resp.data
        fresh_status = int(GatewayTaskStatus(data.status))
        if not _should_update(task.status, fresh_status):
            return task
        result = normalize_generation_result(data.status, data.urls, data.reason)
        task, updated = await apply_generation_result(
            task,
            result,
            source="fallback_poll",
            callback_sent=task.callback_sent,
        )
        if updated:
            logger.info(
                "generate.fallback_poll.updated",
                task_id=task.id,
                status=int(task.status),
            )
    except AppError:
        raise
    except Exception as exc:
        logger.warning(
            "generate.fallback_poll.error",
            task_id=task.id,
            union_task_id=task.union_task_id,
            error=str(exc),
        )
        raise AppError(
            ErrorCode.GENERATION_STATUS_UNAVAILABLE,
            "查询生成任务状态失败",
            {"task_id": task.id},
        ) from exc


async def _fetch_queue_info(task_id: int, union_task_id: int) -> Optional[GatewayQueueItem]:
    try:
        response = await gateway_client.get_tasks_queue([union_task_id])
        return next((item for item in response.data.tasks if item.task_id == union_task_id), None)
    except Exception as exc:
        logger.warning(
            "generate.queue_info.error",
            task_id=task_id,
            union_task_id=union_task_id,
            error=str(exc),
        )
        raise AppError(
            ErrorCode.GENERATION_QUEUE_UNAVAILABLE,
            "查询生成队列状态失败",
            {"task_id": task_id},
        ) from exc


async def _fetch_queue_info_map(tasks: list[GenerateTask]) -> dict[int, GatewayQueueItem]:
    union_task_ids = [
        int(t.union_task_id)
        for t in tasks
        if t.status in NON_TERMINAL_STATUSES and t.union_task_id
    ]
    if not union_task_ids:
        return {}

    try:
        response = await gateway_client.get_tasks_queue(union_task_ids)
        return {
            item.task_id: item
            for item in response.data.tasks
        }
    except Exception as exc:
        logger.warning(
            "generate.queue_info_map.error",
            task_ids=[t.id for t in tasks],
            union_task_ids=union_task_ids,
            error=str(exc),
        )
        raise AppError(
            ErrorCode.GENERATION_QUEUE_UNAVAILABLE,
            "查询生成队列状态失败",
            {"task_ids": [int(task.id) for task in tasks]},
        ) from exc


async def _reconcile_canvas(task: GenerateTask, source: str) -> Any:
    return await reconcile_canvas_for_task(task, source=source)


async def get_generate_task_status(
    task_id: int,
    user_id: int,
    *,
    fallback_poll: FallbackPoll = _fallback_poll,
    fetch_queue_info: QueueInfo = _fetch_queue_info,
    load_ref_materials_map: RefMaterialsMap = _load_ref_materials_map,
    favorite_map_for_tasks: FavoriteMap = _favorite_map_for_tasks,
    reconcile_canvas: CanvasReconcile = _reconcile_canvas,
) -> GenerateTaskView:
    task = await GenerateTask.get_or_none(id=task_id, user_id=user_id, deleted_at__isnull=True)
    if task is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")

    if task.status in NON_TERMINAL_STATUSES and task.union_task_id:
        task = await fallback_poll(task)

    if task.status in TERMINAL_STATUSES:
        await reconcile_canvas(task, "get_task_status")

    queue_info: Optional[GatewayQueueItem] = None
    if task.status in NON_TERMINAL_STATUSES and task.union_task_id:
        queue_info = await fetch_queue_info(task.id, task.union_task_id)

    ref_materials_map = await load_ref_materials_map([task])
    favorite_map = await favorite_map_for_tasks([task])
    return _task_to_view(
        task,
        queue_info=queue_info,
        ref_materials=ref_materials_map.get(task.id, []),
        is_favorited=favorite_map.get(int(task.id), False),
    )


async def get_generate_tasks_status(
    task_ids: list[int],
    user_id: int,
    *,
    fallback_poll: FallbackPoll = _fallback_poll,
    fetch_queue_info_map: QueueInfoMap = _fetch_queue_info_map,
    load_ref_materials_map: RefMaterialsMap = _load_ref_materials_map,
    favorite_map_for_tasks: FavoriteMap = _favorite_map_for_tasks,
    reconcile_canvas: CanvasReconcile = _reconcile_canvas,
) -> GenerateTasksStatusResponse:
    requested_ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
    tasks = await GenerateTask.filter(
        id__in=requested_ids,
        user_id=user_id,
        deleted_at__isnull=True,
    ).all()
    task_by_id = {int(task.id): task for task in tasks}
    missing_task_ids = [task_id for task_id in requested_ids if task_id not in task_by_id]

    poll_tasks = [
        task
        for task in tasks
        if task.status in NON_TERMINAL_STATUSES and task.union_task_id
    ]
    if poll_tasks:
        refreshed = await asyncio.gather(*(fallback_poll(task) for task in poll_tasks))
        task_by_id.update({int(task.id): task for task in refreshed})

    ordered_tasks = [
        task_by_id[task_id]
        for task_id in requested_ids
        if task_id in task_by_id
    ]
    terminal_tasks = [task for task in ordered_tasks if task.status in TERMINAL_STATUSES]
    if terminal_tasks:
        await asyncio.gather(
            *(reconcile_canvas(task, "get_tasks_status") for task in terminal_tasks)
        )

    queue_info_map, ref_materials_map, favorite_map = await asyncio.gather(
        fetch_queue_info_map(ordered_tasks),
        load_ref_materials_map(ordered_tasks),
        favorite_map_for_tasks(ordered_tasks),
    )
    return GenerateTasksStatusResponse(
        items=[
            _task_to_view(
                task,
                queue_info=queue_info_map.get(int(task.union_task_id)) if task.union_task_id else None,
                ref_materials=ref_materials_map.get(int(task.id), []),
                is_favorited=favorite_map.get(int(task.id), False),
            )
            for task in ordered_tasks
        ],
        missing_task_ids=missing_task_ids,
    )
