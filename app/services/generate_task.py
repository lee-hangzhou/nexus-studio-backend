from datetime import datetime, timezone

from fastapi import UploadFile

from app.chat.attachments.service import ChatAttachmentService
from app.contracts.gateway import GatewayQueueItem
from app.core.logger import logger
from app.domain.enums import GatewayTaskStatus
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.assets import Assets
from app.models.generate_task import GenerateTask
from app.ports.generation_gateway import GenerationGatewayPort
from app.repositories.asset import AssetRepository
from app.repositories.chat_attachment import ChatAttachmentRepository
from app.repositories.generate_task import GenerateTaskQuery, GenerateTaskRepository
from app.schemas.generate import (
    GenerateCallbackPayload,
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateRefMaterial,
    GenerateTaskCursor,
    GenerateTaskListItem,
    GenerateTaskListRequest,
    GenerateTaskListResponse,
    GenerateTasksStatusResponse,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)
from app.services.generation_assets import (
    asset_task_id,
    ensure_result_assets,
    task_result_asset_ids,
)
from app.services.generation_canvas_bridge import reconcile_canvas_for_task
from app.services.generation_models import list_generate_models
from app.services.generation_params import validate_material_upload
from app.services.generation_result import apply_generation_result, normalize_generation_result
from app.services.generation_submit import submit_generate_task
from app.services.generate_task_views import GenerateTaskViewAssembler

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


class GenerateTaskService:
    def __init__(
        self,
        *,
        task_repository: GenerateTaskRepository,
        asset_repository: AssetRepository,
        attachment_repository: ChatAttachmentRepository,
        gateway_client: GenerationGatewayPort,
        attachment_service: ChatAttachmentService,
        view_assembler: GenerateTaskViewAssembler,
    ) -> None:
        self._task_repository = task_repository
        self._asset_repository = asset_repository
        self._attachment_repository = attachment_repository
        self._gateway_client = gateway_client
        self._attachment_service = attachment_service
        self._view_assembler = view_assembler

    async def upload_material(
        self,
        user_id: int,
        file: UploadFile,
    ) -> GenerateMaterialUploadResponse:
        filename = file.filename or "material"
        mime_type = file.content_type or "application/octet-stream"
        await file.seek(0)
        raw = await file.read()
        validate_material_upload(mime_type, len(raw))
        row = await self._attachment_service.upload_and_enqueue(
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
            url=self._attachment_service.build_preview_url(row.storage_key),
        )

    async def _load_reference_materials_by_task_id(
        self,
        tasks: list[GenerateTask],
        user_id: int,
    ) -> dict[int, list[GenerateRefMaterial]]:
        """读取当前用户可见的任务引用素材"""
        attachment_ids, asset_ids = self._collect_reference_ids(tasks)
        if not attachment_ids and not asset_ids:
            return {}

        attachments = await self._attachment_repository.get_by_ids_for_user(
            attachment_ids,
            user_id,
        )
        assets = await self._asset_repository.get_active_by_ids_for_user(
            asset_ids,
            user_id,
        )
        return self._view_assembler.reference_materials_by_task_id(
            tasks,
            attachments,
            assets,
        )

    @staticmethod
    def _collect_reference_ids(
        tasks: list[GenerateTask],
    ) -> tuple[set[int], set[int]]:
        attachment_ids: set[int] = set()
        asset_ids: set[int] = set()
        for task in tasks:
            attachment_ids.update(task.ref_attachment_ids or [])
            asset_ids.update(task.ref_asset_ids or [])
        return attachment_ids, asset_ids

    async def _load_favorited_asset_ids(
        self,
        tasks: list[GenerateTask],
        user_id: int,
    ) -> set[int]:
        """读取当前用户已收藏的任务结果资产 ID"""
        asset_ids: set[int] = set()
        for task in tasks:
            asset_ids.update(task_result_asset_ids(task))
        if not asset_ids:
            return set()
        rows = await self._asset_repository.get_favorited_by_ids_for_user(
            asset_ids,
            user_id,
        )
        return {row.id for row in rows}

    async def _favorite_task_ids_for_user(self, user_id: int) -> set[int]:
        rows = await self._asset_repository.list_favorited_for_user(user_id)
        task_ids: set[int] = set()
        for row in rows:
            task_id = asset_task_id(row)
            if task_id is not None:
                task_ids.add(task_id)
        return task_ids

    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
        return await submit_generate_task(user_id, req)

    async def get_task_status(self, task_id: int, user_id: int) -> GenerateTaskView:
        """从本地数据库读取单个任务并组装展示数据"""
        result = await self.get_tasks_status([task_id], user_id)
        if not result.items:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        return result.items[0]

    async def get_tasks_status(
        self,
        task_ids: list[int],
        user_id: int,
    ) -> GenerateTasksStatusResponse:
        """按请求顺序批量读取当前用户的本地任务并组装展示数据"""
        requested_ids = list(set(task_ids))
        tasks = await self._task_repository.get_by_ids_for_user(
            requested_ids,
            user_id,
        )
        task_by_id = {task.id: task for task in tasks}
        ordered_tasks = [
            task_by_id[task_id]
            for task_id in requested_ids
            if task_id in task_by_id
        ]
        gateway_task_ids = self._gateway_task_ids_for_queue(ordered_tasks)
        queue_info_by_gateway_task_id = await self._fetch_queue_info(gateway_task_ids)
        reference_materials_by_task_id = await self._load_reference_materials_by_task_id(
            ordered_tasks,
            user_id,
        )
        favorited_asset_ids = await self._load_favorited_asset_ids(
            ordered_tasks,
            user_id,
        )
        items: list[GenerateTaskView] = []
        for task in ordered_tasks:
            queue_info = None
            if task.union_task_id is not None:
                queue_info = queue_info_by_gateway_task_id.get(task.union_task_id)
            items.append(
                self._view_assembler.task_view(
                    task,
                    queue_info=queue_info,
                    ref_materials=reference_materials_by_task_id.get(task.id, []),
                    favorited_asset_ids=favorited_asset_ids,
                )
            )

        return GenerateTasksStatusResponse(
            items=items,
            missing_task_ids=[
                task_id for task_id in requested_ids if task_id not in task_by_id
            ],
        )

    @staticmethod
    def _gateway_task_ids_for_queue(tasks: list[GenerateTask]) -> list[int]:
        gateway_task_ids: list[int] = []
        for task in tasks:
            if task.status not in NON_TERMINAL:
                continue
            if task.union_task_id is None:
                continue
            gateway_task_ids.append(task.union_task_id)
        return gateway_task_ids

    async def _fetch_queue_info(
        self,
        gateway_task_ids: list[int],
    ) -> dict[int, GatewayQueueItem]:
        """批量读取用于展示的网关队列信息"""
        unique_gateway_task_ids = list(set(gateway_task_ids))
        if not unique_gateway_task_ids:
            return {}

        try:
            response = await self._gateway_client.get_tasks_queue(
                unique_gateway_task_ids
            )
            return {item.task_id: item for item in response.data.tasks}
        except Exception as exc:
            logger.warning(
                "generate.queue_info.error",
                gateway_task_ids=unique_gateway_task_ids,
                error=str(exc),
            )
            return {}

    async def list_tasks(
        self,
        user_id: int,
        req: GenerateTaskListRequest,
    ) -> GenerateTaskListResponse:
        task_ids: frozenset[int] | None = None
        if req.favorites_only:
            favorite_task_ids = await self._favorite_task_ids_for_user(user_id)
            if not favorite_task_ids:
                return GenerateTaskListResponse(
                    items=[],
                    next_cursor=None,
                    has_more=False,
                )
            task_ids = frozenset(favorite_task_ids)

        cursor_created_at: datetime | None = None
        cursor_task_id: int | None = None
        if req.cursor is not None:
            cursor_created_at = req.cursor.created_at
            cursor_task_id = req.cursor.task_id

        tasks = await self._task_repository.list_by_query(
            GenerateTaskQuery(
                user_id=user_id,
                limit=req.page_size + 1,
                kind=req.kind,
                statuses=tuple(req.statuses),
                created_after=req.created_after,
                prompt_query=req.query.strip(),
                task_ids=task_ids,
                cursor_created_at=cursor_created_at,
                cursor_task_id=cursor_task_id,
            )
        )

        has_more = len(tasks) > req.page_size
        listed_tasks = tasks[: req.page_size]

        next_cursor: GenerateTaskCursor | None = None
        if has_more and listed_tasks:
            last = listed_tasks[-1]
            next_cursor = GenerateTaskCursor(
                created_at=last.created_at,
                task_id=last.id,
            )

        gateway_task_ids = self._gateway_task_ids_for_queue(listed_tasks)
        queue_info_by_gateway_task_id = await self._fetch_queue_info(
            gateway_task_ids
        )
        favorited_asset_ids = await self._load_favorited_asset_ids(
            listed_tasks,
            user_id,
        )

        items: list[GenerateTaskListItem] = []
        for task in listed_tasks:
            queue_info = None
            if task.union_task_id is not None:
                queue_info = queue_info_by_gateway_task_id.get(task.union_task_id)
            items.append(
                self._view_assembler.task_list_item(
                    task,
                    queue_info=queue_info,
                    favorited_asset_ids=favorited_asset_ids,
                )
            )

        return GenerateTaskListResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def cancel(self, task_id: int, user_id: int) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id, deleted_at__isnull=True)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")

        if task.kind == "image":
            raise AppError(ErrorCode.TASK_CANCEL_FAILED, "图片生成任务暂不支持取消")

        if task.union_task_id:
            try:
                await self._gateway_client.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.error(
                    "generate.cancel.gateway_error",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )
                raise AppError(
                    ErrorCode.TASK_CANCEL_FAILED,
                    "上游任务取消失败，请稍后重试",
                ) from exc

        updated = await GenerateTask.filter(
            id=task_id, status__not_in=list(TERMINAL)
        ).update(status=GatewayTaskStatus.CANCELLED)

        if not updated:
            raise AppError(ErrorCode.TASK_ALREADY_FINISHED, "任务已结束，无法取消")

    async def toggle_favorite(self, task_id: int, user_id: int, favorited: bool) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id, deleted_at__isnull=True)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        if task.status == GatewayTaskStatus.SUCCEEDED and not task_result_asset_ids(task):
            task = await ensure_result_assets(task)
        asset_ids = task_result_asset_ids(task)
        if not asset_ids:
            raise AppError(ErrorCode.INVALID_PARAMS, "任务暂无可收藏资产")
        await Assets.filter(
            user_id=user_id,
            id__in=asset_ids,
            deleted_at__isnull=True,
        ).update(favorite=favorited)

    async def delete_task(self, task_id: int, user_id: int) -> None:
        task = await GenerateTask.get_or_none(id=task_id, user_id=user_id, deleted_at__isnull=True)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")

        if task.status in NON_TERMINAL and task.union_task_id and task.kind == "video":
            try:
                await self._gateway_client.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.warning(
                    "generate.delete.cancel_upstream_failed",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )

        await GenerateTask.filter(id=task.id, deleted_at__isnull=True).update(
            deleted_at=datetime.now(timezone.utc),
        )

    async def list_models(self, kind: str) -> GenerateModelsResponse:
        return await list_generate_models(kind)

    async def handle_callback(self, payload: GenerateCallbackPayload) -> None:
        task = await GenerateTask.get_or_none(union_task_id=payload.task_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "回调任务不存在")
        if task.deleted_at is not None:
            raise AppError(ErrorCode.TASK_DELETED, "回调任务已删除")

        if task.callback_sent and task.status in TERMINAL:
            logger.info(
                "generate.callback.idempotent_skip",
                task_id=task.id,
                union_task_id=payload.task_id,
            )
            await reconcile_canvas_for_task(task, source="callback_idempotent")
            return

        result = normalize_generation_result(payload.status, payload.urls, payload.reason)
        task, applied = await apply_generation_result(
            task,
            result,
            source="callback",
            callback_sent=True,
        )
        if applied:
            logger.info("generate.callback.written", task_id=task.id, status=int(task.status))
            return
        await GenerateTask.filter(id=task.id, deleted_at__isnull=True).update(callback_sent=True)
        await reconcile_canvas_for_task(task, source="callback_already_terminal")
