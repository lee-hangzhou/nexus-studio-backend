"""创作页生成任务服务

负责提交、状态同步、历史查询、取消、收藏等业务逻辑。
所有状态写入均带终态保护 WHERE 约束，确保终态（5/6/7）不被任何路径覆盖。
"""

import base64
import binascii
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from tortoise.expressions import Q

from app.assets.service import (
    asset_service,
)
from app.chat.attachments.service import chat_attachment_service
from app.contracts.gateway import GatewayQueueItem
from app.core.config import settings
from app.core.gateway import gateway_client
from app.core.logger import logger
from app.domain.enums import GatewayTaskStatus
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.assets import Assets
from app.models.chat_attachments import ChatAttachments
from app.models.generate_task import GenerateTask
from app.schemas.generate import (
    GenerateCallbackPayload,
    GenerateHistoryItemView,
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateRefMaterial,
    GenerateTasksStatusResponse,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    HistoryRequest,
    HistoryResponse,
    SubmitGenerateRequest,
)
from app.services.generation_assets import (
    asset_task_id,
    ensure_result_assets,
    task_result_asset_ids,
    to_result_urls,
)
from app.services.generation_canvas_bridge import reconcile_canvas_for_task, sync_canvas_for_task
from app.services.generation_models import list_generate_models
from app.services.generation_params import validate_material_upload
from app.services.generation_status import (
    _fallback_poll as fallback_poll_generate_task,
)
from app.services.generation_status import (
    _fetch_queue_info as fetch_generate_queue_info,
)
from app.services.generation_status import (
    get_generate_task_status,
    get_generate_tasks_status,
)
from app.services.generation_submit import submit_generate_task

# 终态集合：不可被任何路径覆盖
TERMINAL: set[int] = {
    GatewayTaskStatus.SUCCEEDED,
    GatewayTaskStatus.FAILED,
    GatewayTaskStatus.CANCELLED,
}
NON_TERMINAL: set[int] = {
    GatewayTaskStatus.CREATED,
    GatewayTaskStatus.QUEUED,
    GatewayTaskStatus.WAITING,
    GatewayTaskStatus.RUNNING,
}

def _status_label(status: int) -> str:
    """DB 状态整数 → 前端 status 字符串"""
    if status in (GatewayTaskStatus.CREATED, GatewayTaskStatus.QUEUED, GatewayTaskStatus.WAITING):
        return "pending"
    if status == GatewayTaskStatus.RUNNING:
        return "running"
    if status == GatewayTaskStatus.SUCCEEDED:
        return "success"
    return "failed"  # FAILED(6) 和 CANCELLED(7) 均展示为失败


def _encode_cursor(created_at: datetime, row_id: int) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(ts_str), int(id_str)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, "无效的 cursor") from exc


def _task_to_history_item(
    task: GenerateTask,
    queue_info: Optional[GatewayQueueItem] = None,
    is_favorited: Optional[bool] = None,
) -> GenerateHistoryItemView:
    preview_url: Optional[str] = None
    preview_media_type: Optional[int] = None
    result_count = 0
    if task.result_keys:
        signed = to_result_urls(task.result_keys)
        result_count = len(signed)
        if signed:
            first = signed[0]
            preview_url = first.url
            preview_media_type = first.type
    preview_asset_id: Optional[int] = None
    if isinstance(task.result_asset_ids, list) and task.result_asset_ids:
        preview_asset_id = int(task.result_asset_ids[0])

    view = GenerateHistoryItemView(
        task_id=task.id,
        kind=task.kind,
        status=_status_label(task.status),
        prompt=task.prompt,
        model_id=task.model_id,
        ratio=task.ratio,
        resolution=task.resolution,
        duration=task.duration,
        reference_mode=task.reference_mode,
        result_count=result_count,
        preview_url=preview_url,
        preview_asset_id=preview_asset_id,
        preview_media_type=preview_media_type,
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


def _task_result_asset_ids(task: GenerateTask) -> list[int]:
    return task_result_asset_ids(task)


def _asset_task_id(row: Assets) -> int | None:
    return asset_task_id(row)


class GenerateService:

    async def upload_material(self, user_id: int, file: UploadFile) -> GenerateMaterialUploadResponse:
        filename = file.filename or "material"
        mime_type = file.content_type or "application/octet-stream"
        await file.seek(0)
        raw = await file.read()
        validate_material_upload(mime_type, len(raw))
        row = await chat_attachment_service.upload_and_enqueue(
            user_id=user_id,
            conversation_id=0,
            filename=filename,
            mime_type=mime_type,
            raw_bytes=raw,
        )
        return GenerateMaterialUploadResponse(
            material_id=row.id,
            asset_id=row.asset_id,
            filename=row.filename,
            mime_type=row.mime_type,
            url=chat_attachment_service.build_preview_url(row.storage_key),
        )

    async def _load_ref_materials_map(
        self, tasks: list[GenerateTask]
    ) -> dict[int, list[GenerateRefMaterial]]:
        """批量回读任务引用的素材（id/文件名/mime/加签 URL），用于"再次生成 / 重新编辑"。

        素材可能已被删除：缺失的 id 直接跳过，不报错（fail soft，仅影响回显）。
        """
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

    async def _favorite_map_for_tasks(self, tasks: list[GenerateTask]) -> dict[int, bool]:
        asset_ids: set[int] = set()
        for task in tasks:
            asset_ids.update(_task_result_asset_ids(task))
        if not asset_ids:
            return {int(task.id): False for task in tasks}
        rows = await Assets.filter(id__in=list(asset_ids), deleted_at__isnull=True).all()
        favorite_asset_ids = {int(row.id) for row in rows if bool(row.favorite)}
        result: dict[int, bool] = {}
        for task in tasks:
            result[int(task.id)] = any(asset_id in favorite_asset_ids for asset_id in _task_result_asset_ids(task))
        return result

    async def _favorite_task_ids_for_user(self, user_id: int) -> set[int]:
        rows = await Assets.filter(user_id=user_id, favorite=True, deleted_at__isnull=True).all()
        task_ids: set[int] = set()
        for row in rows:
            task_id = _asset_task_id(row)
            if task_id is not None:
                task_ids.add(task_id)
        return task_ids

    async def _ensure_result_assets(self, task: GenerateTask) -> list[int]:
        updated = await ensure_result_assets(task)
        return _task_result_asset_ids(updated)

    async def _fallback_poll(self, task: GenerateTask) -> GenerateTask:
        return await fallback_poll_generate_task(task)

    async def _fetch_queue_info(self, task_id: int, union_task_id: int) -> Optional[GatewayQueueItem]:
        return await fetch_generate_queue_info(task_id, union_task_id)

    async def _reconcile_canvas_task(self, task: GenerateTask, source: str) -> Any:
        return await reconcile_canvas_for_task(task, source=source)

    # ── 提交 ──────────────────────────────────────────────────────────────────

    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
        return await submit_generate_task(user_id, req)

    # ── 单任务状态查询（含兜底轮询）────────────────────────────────────────────

    async def get_task_status(self, task_id: int, user_id: int) -> GenerateTaskView:
        return await get_generate_task_status(
            task_id,
            user_id,
            fallback_poll=self._fallback_poll,
            fetch_queue_info=self._fetch_queue_info,
            load_ref_materials_map=self._load_ref_materials_map,
            favorite_map_for_tasks=self._favorite_map_for_tasks,
            reconcile_canvas=self._reconcile_canvas_task,
        )

    async def get_tasks_status(
        self,
        task_ids: list[int],
        user_id: int,
    ) -> GenerateTasksStatusResponse:
        return await get_generate_tasks_status(
            task_ids,
            user_id,
            fallback_poll=self._fallback_poll,
            fetch_queue_info_map=self._fetch_queue_info_map,
            load_ref_materials_map=self._load_ref_materials_map,
            favorite_map_for_tasks=self._favorite_map_for_tasks,
            reconcile_canvas=self._reconcile_canvas_task,
        )

    async def _fetch_queue_info_map(
        self,
        tasks: list[GenerateTask],
    ) -> dict[int, GatewayQueueItem]:
        union_task_ids = [
            int(t.union_task_id)
            for t in tasks
            if t.status in NON_TERMINAL and t.union_task_id
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
            return {}

    # ── 历史列表（cursor 分页）────────────────────────────────────────────────

    async def list_history(self, user_id: int, req: HistoryRequest) -> HistoryResponse:
        qs = GenerateTask.filter(user_id=user_id)

        if req.kind != "all":
            qs = qs.filter(kind=req.kind)

        if req.status != "all":
            status_values = _status_filter_to_db(req.status)
            qs = qs.filter(status__in=status_values)

        if req.time_range != "all":
            cutoff = _time_range_cutoff(req.time_range)
            qs = qs.filter(created_at__gte=cutoff)

        if req.query:
            qs = qs.filter(prompt__icontains=req.query)

        if req.favorites_only:
            favorite_task_ids = await self._favorite_task_ids_for_user(user_id)
            if not favorite_task_ids:
                return HistoryResponse(items=[], next_cursor=None, has_more=False)
            qs = qs.filter(id__in=list(favorite_task_ids))

        if req.cursor:
            cursor_ts, cursor_id = _decode_cursor(req.cursor)
            qs = qs.filter(
                Q(created_at__lt=cursor_ts)
                | Q(created_at=cursor_ts, id__lt=cursor_id)
            )

        tasks = await qs.order_by("-created_at", "-id").limit(req.page_size + 1)

        has_more = len(tasks) > req.page_size
        page_tasks = tasks[: req.page_size]

        next_cursor: Optional[str] = None
        if has_more and page_tasks:
            last = page_tasks[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)

        queue_info_map = await self._fetch_queue_info_map(page_tasks)
        favorite_map = await self._favorite_map_for_tasks(page_tasks)

        return HistoryResponse(
            items=[
                _task_to_history_item(
                    t,
                    queue_info=queue_info_map.get(int(t.union_task_id)) if t.union_task_id else None,
                    is_favorited=favorite_map.get(int(t.id), False),
                )
                for t in page_tasks
            ],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    # ── 取消 ──────────────────────────────────────────────────────────────────

    async def cancel(self, task_id: int, user_id: int) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")

        if task.kind == "image":
            raise AppError(ErrorCode.TASK_CANCEL_FAILED, "图片生成任务暂不支持取消")

        if task.union_task_id:
            try:
                await gateway_client.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.error(
                    "generate.cancel.gateway_error",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )
                raise AppError(ErrorCode.TASK_CANCEL_FAILED, "上游任务取消失败，请稍后重试") from exc

        updated = await GenerateTask.filter(
            id=task_id, status__not_in=list(TERMINAL)
        ).update(status=GatewayTaskStatus.CANCELLED)

        if not updated:
            raise AppError(ErrorCode.TASK_ALREADY_FINISHED, "任务已结束，无法取消")

    # ── 收藏 ──────────────────────────────────────────────────────────────────

    async def toggle_favorite(self, task_id: int, user_id: int, favorited: bool) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        if task.status == GatewayTaskStatus.SUCCEEDED and not _task_result_asset_ids(task):
            task = await ensure_result_assets(task)
        asset_ids = _task_result_asset_ids(task)
        if not asset_ids:
            raise AppError(ErrorCode.INVALID_PARAMS, "任务暂无可收藏资产")
        await Assets.filter(
            user_id=user_id,
            id__in=asset_ids,
            deleted_at__isnull=True,
        ).update(favorite=favorited)

    async def delete_task(self, task_id: int, user_id: int) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")

        if task.status in NON_TERMINAL and task.union_task_id and task.kind == "video":
            try:
                await gateway_client.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.warning(
                    "generate.delete.cancel_upstream_failed",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )

        await task.delete()

    # ── 模型列表 ──────────────────────────────────────────────────────────────

    async def list_models(self, kind: str) -> GenerateModelsResponse:
        return await list_generate_models(kind)

    # ── 回调处理（内网专用）──────────────────────────────────────────────────

    async def handle_callback(self, payload: GenerateCallbackPayload) -> None:
        task = await GenerateTask.get_or_none(union_task_id=payload.task_id)
        if task is None:
            logger.warning("generate.callback.task_not_found", union_task_id=payload.task_id)
            return

        if task.callback_sent and task.status in TERMINAL:
            logger.info(
                "generate.callback.idempotent_skip",
                task_id=task.id,
                union_task_id=payload.task_id,
            )
            await reconcile_canvas_for_task(task, source="callback_idempotent")
            return

        mapped_status = int(payload.status)
        update_kwargs: dict[str, Any] = {
            "status": mapped_status,
            "callback_sent": True,
        }
        if payload.urls:
            update_kwargs["result_keys"] = [
                item.model_dump(mode="json", exclude_none=True)
                for item in payload.urls
            ]
        if payload.reason:
            update_kwargs["error_message"] = payload.reason

        updated = await GenerateTask.filter(
            union_task_id=payload.task_id,
            status__not_in=list(TERMINAL),
        ).update(**update_kwargs)

        if updated:
            logger.info(
                "generate.callback.written",
                task_id=task.id,
                status=mapped_status,
            )
            task = await GenerateTask.get(id=task.id)
            if int(task.status) == int(GatewayTaskStatus.SUCCEEDED):
                task = await ensure_result_assets(task)
            await sync_canvas_for_task(task, source="callback")
        else:
            logger.warning(
                "generate.callback.already_terminal",
                task_id=task.id,
                current_status=task.status,
                incoming_status=mapped_status,
            )
            ack_fields: dict[str, Any] = {"callback_sent": True}
            if payload.urls:
                ack_fields["result_keys"] = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in payload.urls
                ]
            await GenerateTask.filter(union_task_id=payload.task_id).update(**ack_fields)
            task = await GenerateTask.get(id=task.id)
            await reconcile_canvas_for_task(task, source="callback_already_terminal")


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _status_filter_to_db(status_filter: str) -> list[int]:
    if status_filter == "in_progress":
        return list(NON_TERMINAL)
    if status_filter == "success":
        return [GatewayTaskStatus.SUCCEEDED]
    if status_filter == "failed":
        return [GatewayTaskStatus.FAILED, GatewayTaskStatus.CANCELLED]
    return []


def _time_range_cutoff(time_range: str) -> datetime:
    tz = ZoneInfo(settings.CHAT_CONTEXT_TIMEZONE)
    now_local = datetime.now(tz)
    if time_range == "today":
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(timezone.utc)
    if time_range == "week":
        return (now_local - timedelta(days=7)).astimezone(timezone.utc)
    if time_range == "month":
        return (now_local - timedelta(days=30)).astimezone(timezone.utc)
    return now_local.astimezone(timezone.utc)  # fallback，不应到达


generate_service = GenerateService()
