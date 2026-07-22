from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import UploadFile

from app.server.chat.services.attachments.service import ChatAttachmentService
from app.server.infra.logger import logger
from app.server.generation.domain.gateway_status import TERMINAL_GATEWAY_TASK_STATUSES, GatewayTaskStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.assets.persistence.assets import Assets
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.ports.generation_gateway import GenerationGatewayPort
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.persistence.repository import GenerateTaskQuery, GenerateTaskRepository
from app.server.generation.schemas import (
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
from app.server.generation.services.generation_assets import (
    asset_task_id,
    ensure_result_assets,
    task_result_asset_ids,
)
from app.server.generation.services.generation_models import list_generate_models
from app.server.generation.services.generation_observation import (
    GatewayObservationBatch,
    GatewayQueueObservation,
)
from app.server.generation.services.generation_params import validate_material_upload
from app.server.generation.services.generation_result import apply_generation_result, normalize_generation_result
from app.server.generation.services.generation_submit import submit_generate_task
from app.server.generation.services.generate_task_views import GenerateTaskViewAssembler


@dataclass(frozen=True)
class GenerationCallbackOutcome:
    task: GenerateTask
    applied: bool


@dataclass(frozen=True)
class ObservedGenerateTask:
    task: GenerateTask
    observation: GatewayQueueObservation | None


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
        """观察单个任务并组装展示视图；非终态时对账网关"""
        observed = await self.observe_task(task_id, user_id)
        return await self.assemble_task_view(observed, user_id)

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerateTask:
        """读取本地任务并对非终态做网关观察写回，返回实体与队列观察结果"""
        tasks = await self._task_repository.get_by_ids_for_user([task_id], user_id)
        if not tasks:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        observed_tasks, observations = await self._observe_non_terminal_tasks(tasks)
        task = observed_tasks[0]
        observation = None
        if task.union_task_id is not None:
            observation = observations.for_union_task(task.union_task_id)
        return ObservedGenerateTask(task=task, observation=observation)

    async def assemble_task_view(
        self,
        observed: ObservedGenerateTask,
        user_id: int,
    ) -> GenerateTaskView:
        """将已观察任务组装为对外视图（引用素材、收藏、队列展示字段）"""
        tasks = [observed.task]
        reference_materials_by_task_id = await self._load_reference_materials_by_task_id(
            tasks,
            user_id,
        )
        favorited_asset_ids = await self._load_favorited_asset_ids(tasks, user_id)
        return self._view_assembler.task_view(
            observed.task,
            observation=observed.observation,
            ref_materials=reference_materials_by_task_id.get(observed.task.id, []),
            favorited_asset_ids=favorited_asset_ids,
        )

    async def get_tasks_status(
        self,
        task_ids: list[int],
        user_id: int,
    ) -> GenerateTasksStatusResponse:
        """批量观察任务状态；非终态经网关对账后返回，终态只读本地"""
        requested_ids = list(dict.fromkeys(task_ids))
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
        ordered_tasks, observations = await self._observe_non_terminal_tasks(ordered_tasks)
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
            observation = None
            if task.union_task_id is not None:
                observation = observations.for_union_task(task.union_task_id)
            items.append(
                self._view_assembler.task_view(
                    task,
                    observation=observation,
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

    async def _observe_non_terminal_tasks(
        self,
        tasks: list[GenerateTask],
    ) -> tuple[list[GenerateTask], GatewayObservationBatch]:
        """对非终态任务做一次网关观察：不一致则 CAS 写回，并返回队列展示信息"""
        # 仅观察仍可推进且已绑定网关任务的本地任务
        union_task_ids = [
            task.union_task_id
            for task in tasks
            if GatewayTaskStatus(task.status).is_non_terminal and task.union_task_id is not None
        ]
        observations = await self._fetch_observations(union_task_ids)
        observed: list[GenerateTask] = []
        for task in tasks:
            observed.append(await self._observe_one_non_terminal_task(task, observations))
        return observed, observations

    async def _observe_one_non_terminal_task(
        self,
        task: GenerateTask,
        observations: GatewayObservationBatch,
    ) -> GenerateTask:
        if GatewayTaskStatus(task.status).is_terminal or task.union_task_id is None:
            return task
        observation = observations.for_union_task(task.union_task_id)
        if observation is None:
            return task
        if observation.status == GatewayTaskStatus(task.status):
            return task
        return await self._apply_gateway_status_observation(task, observation.status)

    async def _apply_gateway_status_observation(
        self,
        task: GenerateTask,
        gateway_status: GatewayTaskStatus,
    ) -> GenerateTask:
        if gateway_status.is_terminal:
            updated_task, applied = await self._apply_gateway_terminal_observation(task)
        else:
            # 中间态推进：尚未收到终态回调
            result = normalize_generation_result(gateway_status, None, None)
            updated_task, applied = await apply_generation_result(
                task,
                result,
                callback_sent=False,
            )
        if applied:
            logger.info(
                "generate.status_observe.written",
                task_id=updated_task.id,
                status=int(updated_task.status),
            )
        return updated_task

    async def _apply_gateway_terminal_observation(
        self,
        task: GenerateTask,
    ) -> tuple[GenerateTask, bool]:
        if task.union_task_id is None:
            return task, False
        try:
            response = await self._gateway_client.get_task(task.union_task_id)
        except Exception as exc:
            logger.warning(
                "generate.status_observe.get_task_error",
                task_id=task.id,
                union_task_id=task.union_task_id,
                error=str(exc),
            )
            return task, False

        data = response.data
        try:
            result = normalize_generation_result(data.status, data.urls, data.reason)
        except AppError as exc:
            logger.warning(
                "generate.status_observe.invalid_terminal_payload",
                task_id=task.id,
                union_task_id=task.union_task_id,
                gateway_status=int(data.status),
                error=str(exc),
            )
            return task, False

        # 观察路径已拿到终态完整载荷，标记为已应用，避免回调重复写入
        return await apply_generation_result(
            task,
            result,
            callback_sent=True,
        )

    async def _fetch_observations(
        self,
        union_task_ids: list[int],
    ) -> GatewayObservationBatch:
        """批量读取网关任务观察结果（状态 + 队列展示字段）"""
        unique_ids = list(dict.fromkeys(union_task_ids))
        if not unique_ids:
            return GatewayObservationBatch.empty()

        try:
            response = await self._gateway_client.get_tasks_queue(unique_ids)
            return GatewayObservationBatch.from_queue_items(response.data.tasks)
        except Exception as exc:
            logger.warning(
                "generate.status_observe.queue_error",
                gateway_task_ids=unique_ids,
                error=str(exc),
            )
            return GatewayObservationBatch.empty()

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

        listed_tasks, observations = await self._observe_non_terminal_tasks(listed_tasks)
        favorited_asset_ids = await self._load_favorited_asset_ids(
            listed_tasks,
            user_id,
        )

        items: list[GenerateTaskListItem] = []
        for task in listed_tasks:
            observation = None
            if task.union_task_id is not None:
                observation = observations.for_union_task(task.union_task_id)
            items.append(
                self._view_assembler.task_list_item(
                    task,
                    observation=observation,
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
            id=task_id,
            status__not_in=[int(status) for status in TERMINAL_GATEWAY_TASK_STATUSES],
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

        if (
            GatewayTaskStatus(task.status).is_non_terminal
            and task.union_task_id
            and task.kind == "video"
        ):
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

    async def handle_callback(
        self,
        payload: GenerateCallbackPayload,
    ) -> GenerationCallbackOutcome:
        """写入 generate_task 回调结果；不投影画布"""
        task = await GenerateTask.get_or_none(union_task_id=payload.task_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "回调任务不存在")
        if task.deleted_at is not None:
            raise AppError(ErrorCode.TASK_DELETED, "回调任务已删除")

        if task.callback_sent and GatewayTaskStatus(task.status).is_terminal:
            logger.info(
                "generate.callback.idempotent_skip",
                task_id=task.id,
                union_task_id=payload.task_id,
            )
            return GenerationCallbackOutcome(task=task, applied=False)

        result = normalize_generation_result(payload.status, payload.urls, payload.reason)
        # 网关终态回调：写入结果并标记 callback_sent，保证幂等
        task, applied = await apply_generation_result(
            task,
            result,
            callback_sent=True,
        )
        if applied:
            logger.info("generate.callback.written", task_id=task.id, status=int(task.status))
            return GenerationCallbackOutcome(task=task, applied=True)

        await GenerateTask.filter(id=task.id, deleted_at__isnull=True).update(callback_sent=True)
        task = await GenerateTask.get(id=task.id)
        return GenerationCallbackOutcome(task=task, applied=False)
