from __future__ import annotations

from typing import Any
from uuid import uuid4

from tortoise.transactions import in_transaction

from app.contracts.gateway import (
    GatewayGenerateMaterial,
    GatewayModelItem,
    GatewayResultItem,
    GatewayVoiceItem,
)
from app.server.assets.persistence.repository import AssetRepository
from app.server.assets.services.service import (
    ASSET_SOURCE_GENERATE_RESULT,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    AssetService,
)
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.chat.services.attachments.service import ChatAttachmentService
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.constants import (
    MODEL_LIST_CACHE_KEY_PREFIX,
    RESULT_ASSET_META_DURATION,
    RESULT_ASSET_META_GATEWAY_RESULT,
    RESULT_ASSET_META_INDEX,
    RESULT_ASSET_META_KIND,
    RESULT_ASSET_META_MODEL_ID,
    RESULT_ASSET_META_PROMPT,
    RESULT_ASSET_META_RATIO,
    RESULT_ASSET_META_RESOLUTION,
    RESULT_ASSET_META_TASK_ID,
    RESULT_ASSET_META_UNION_TASK_ID,
    RESULT_MIME_BY_KIND,
    UNSCOPED_ATTACHMENT_CONVERSATION_ID,
)
from app.server.generation.domain.enums import GenerationKind, MaterialType
from app.server.generation.domain.gateway_status import (
    TERMINAL_GATEWAY_TASK_STATUSES,
    GatewayTaskStatus,
)
from app.server.generation.domain.models import GenerationModelCapabilities
from app.server.generation.domain.rules import (
    material_type_from_mime,
    validate_material_upload,
    validate_reference_materials,
    validate_submit_params,
)
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.generation.persistence.repository import GenerateTaskQuery, GenerateTaskRepository
from app.server.generation.schemas import (
    GenerateCallbackPayload,
    GenerateMaterialUploadResponse,
    GenerateModelItem,
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
from app.server.generation.schemas.observation import (
    GatewayObservationBatch,
    GatewayQueueObservation,
    ObservedGenerateTask,
)
from app.server.generation import assembly
from app.server.generation.domain.terminal import GenerationTerminal, normalize_gateway_terminal
from app.server.generation.gateway.submit import (
    build_image_submit_request,
    build_tts_submit_request,
    build_video_submit_request,
    dedupe_materials,
    require_video_reference_mode,
)
from app.server.generation.schemas.callback import GenerationCallbackResult
from app.server.infra.config import settings
from app.server.infra.logger import log_exception, logger
from app.server.infra.object_storage import TosObjectStorage
from app.server.infra.gateway_mapping import resolve_gateway_failure_message
from app.server.ports.generation_gateway import GenerationGatewayPort
from app.utils.cache import MultiLevelCache


class GenerationService:
    def __init__(
        self,
        *,
        task_repository: GenerateTaskRepository,
        asset_repository: AssetRepository,
        attachment_repository: ChatAttachmentRepository,
        gateway_client: GenerationGatewayPort,
        attachment_service: ChatAttachmentService,
        asset_service: AssetService,
        object_storage: TosObjectStorage,
        model_cache: MultiLevelCache,
    ) -> None:
        self._tasks = task_repository
        self._assets = asset_repository
        self._attachments = attachment_repository
        self._gateway = gateway_client
        self._attachment_service = attachment_service
        self._asset_service = asset_service
        self._object_storage = object_storage
        self._model_cache = model_cache
        self._capabilities: dict[str, GenerationModelCapabilities] = {
            config.model_id: config.to_domain()
            for config in settings.GENERATION_MODEL_CAPABILITIES
        }

    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
        capabilities = await self.require_model_capabilities(req.model_id, req.kind)
        validate_submit_params(
            kind=req.kind,
            model_id=req.model_id,
            capabilities=capabilities,
            ratio=req.ratio,
            resolution=req.resolution,
            count=req.count,
            duration=req.duration,
            reference_mode=req.reference_mode,
        )
        materials: list[GatewayGenerateMaterial] = []
        if req.kind != GenerationKind.AUDIO:
            materials = await self._load_reference_materials(user_id, req, capabilities)

        voice_id: str | None = None
        if req.kind == GenerationKind.AUDIO:
            voice_id = await self.resolve_tts_voice_id(req.model_id, voice_id=req.voice_id)

        reference_mode = None
        if req.kind == GenerationKind.VIDEO:
            reference_mode = require_video_reference_mode(req)

        task = await self._tasks.create(
            user_id=user_id,
            kind=req.kind.value,
            status=GatewayTaskStatus.CREATED,
            prompt=req.prompt,
            model_id=req.model_id,
            voice_id=voice_id,
            ratio=req.ratio,
            resolution=req.resolution,
            max_images=req.count if req.kind == GenerationKind.IMAGE else None,
            duration=req.duration if req.kind == GenerationKind.VIDEO else None,
            reference_mode=reference_mode,
            ref_attachment_ids=list(dict.fromkeys(req.ref_attachment_ids)) or None,
            ref_asset_ids=list(dict.fromkeys(req.ref_asset_ids)) or None,
        )
        logger.info("generate.submit.created", task_id=task.id, kind=req.kind, user_id=user_id)

        try:
            union_task_id = await self._submit_to_gateway(req, materials, voice_id=voice_id)
        except AppError as exc:
            logger.warning(
                "generate.submit.gateway_rejected",
                task_id=task.id,
                error_code=exc.code,
                error_message=exc.message,
            )
            await self._tasks.mark_failed_if_non_terminal(task.id, exc.message)
            raise
        except Exception as exc:
            log_exception(
                "generate.submit.gateway_error",
                exc=exc,
                task_id=task.id,
            )
            await self._tasks.mark_failed_if_non_terminal(task.id, "upstream unavailable")
            raise AppError(
                ErrorCode.GATEWAY_SUBMIT_ERROR,
                "提交生成任务失败：上游服务暂不可用",
                {"task_id": task.id},
            ) from exc

        await self._tasks.bind_union_task_queued(task.id, union_task_id)
        task = await self._tasks.get_by_id_required(task.id)
        logger.info(
            "generate.submit.ok",
            task_id=task.id,
            union_task_id=union_task_id,
            status=task.status,
        )
        return GenerateTaskSubmitResponse(task_id=task.id, status=task.status)

    async def upload_material(
        self,
        user_id: int,
        *,
        filename: str,
        mime_type: str,
        raw_bytes: bytes,
    ) -> GenerateMaterialUploadResponse:
        name = filename.strip()
        content_type = mime_type.strip()
        if not name:
            raise AppError(ErrorCode.INVALID_PARAMS, "素材文件名不能为空")
        if not content_type:
            raise AppError(ErrorCode.INVALID_PARAMS, "素材 Content-Type 不能为空")
        validate_material_upload(mime_type=content_type, size=len(raw_bytes))
        row = await self._attachment_service.upload_and_enqueue(
            user_id=user_id,
            conversation_id=UNSCOPED_ATTACHMENT_CONVERSATION_ID,
            filename=name,
            mime_type=content_type,
            raw_bytes=raw_bytes,
        )
        return GenerateMaterialUploadResponse(
            material_id=row.id,
            asset_id=row.asset_id,
            filename=row.filename,
            mime_type=row.mime_type,
            url=self._attachment_service.build_preview_url(row.storage_key),
        )

    async def get_task_status(self, task_id: int, user_id: int) -> GenerateTaskView:
        observed = await self.observe_task(task_id, user_id)
        return await self.assemble_task_view(observed, user_id)

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerateTask:
        tasks = await self._tasks.get_by_ids_for_user([task_id], user_id)
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
        tasks = [observed.task]
        refs = await self._load_reference_materials_by_task_id(tasks, user_id)
        favorited = await self._load_favorited_asset_ids(tasks, user_id)
        return assembly.to_task_view(
            observed.task,
            observation=observed.observation,
            ref_materials=refs.get(observed.task.id, []),
            favorited_asset_ids=favorited,
            presign=self._object_storage.presigned_get_url,
        )

    async def get_tasks_status(
        self,
        task_ids: list[int],
        user_id: int,
    ) -> GenerateTasksStatusResponse:
        requested_ids = list(dict.fromkeys(task_ids))
        tasks = await self._tasks.get_by_ids_for_user(requested_ids, user_id)
        task_by_id = {task.id: task for task in tasks}
        ordered = [task_by_id[task_id] for task_id in requested_ids if task_id in task_by_id]
        ordered, observations = await self._observe_non_terminal_tasks(ordered)
        refs = await self._load_reference_materials_by_task_id(ordered, user_id)
        favorited = await self._load_favorited_asset_ids(ordered, user_id)
        items: list[GenerateTaskView] = []
        for task in ordered:
            observation = self._observation_for(task, observations)
            items.append(
                assembly.to_task_view(
                    task,
                    observation=observation,
                    ref_materials=refs.get(task.id, []),
                    favorited_asset_ids=favorited,
                    presign=self._object_storage.presigned_get_url,
                )
            )
        return GenerateTasksStatusResponse(
            items=items,
            missing_task_ids=[task_id for task_id in requested_ids if task_id not in task_by_id],
        )

    async def list_tasks(
        self,
        user_id: int,
        req: GenerateTaskListRequest,
    ) -> GenerateTaskListResponse:
        task_ids: frozenset[int] | None = None
        if req.favorites_only:
            favorite_task_ids = await self._favorite_task_ids_for_user(user_id)
            if not favorite_task_ids:
                return GenerateTaskListResponse(items=[], next_cursor=None, has_more=False)
            task_ids = frozenset(favorite_task_ids)

        cursor_created_at = req.cursor.created_at if req.cursor is not None else None
        cursor_task_id = req.cursor.task_id if req.cursor is not None else None

        tasks = await self._tasks.list_by_query(
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
        listed = tasks[: req.page_size]
        next_cursor: GenerateTaskCursor | None = None
        if has_more and listed:
            last = listed[-1]
            next_cursor = GenerateTaskCursor(created_at=last.created_at, task_id=last.id)

        listed, observations = await self._observe_non_terminal_tasks(listed)
        favorited = await self._load_favorited_asset_ids(listed, user_id)
        items: list[GenerateTaskListItem] = []
        for task in listed:
            observation = self._observation_for(task, observations)
            items.append(
                assembly.to_task_list_item(
                    task,
                    observation=observation,
                    favorited_asset_ids=favorited,
                    presign=self._object_storage.presigned_get_url,
                )
            )
        return GenerateTaskListResponse(items=items, next_cursor=next_cursor, has_more=has_more)

    async def cancel(self, task_id: int, user_id: int) -> None:
        task = await self._tasks.get_active_for_user(task_id, user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        if GenerationKind(task.kind) == GenerationKind.IMAGE:
            raise AppError(ErrorCode.TASK_CANCEL_FAILED, "图片生成任务暂不支持取消")
        if task.union_task_id:
            try:
                await self._gateway.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.error(
                    "generate.cancel.gateway_error",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )
                raise AppError(ErrorCode.TASK_CANCEL_FAILED, "上游任务取消失败，请稍后重试") from exc
        if not await self._tasks.cancel_if_non_terminal(task_id):
            raise AppError(ErrorCode.TASK_ALREADY_FINISHED, "任务已结束，无法取消")

    async def toggle_favorite(self, task_id: int, user_id: int, favorited: bool) -> None:
        task = await self._tasks.get_active_for_user(task_id, user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        if task.status == GatewayTaskStatus.SUCCEEDED and not assembly.task_result_asset_ids(task):
            task = await self.ensure_result_assets(task)
        asset_ids = assembly.task_result_asset_ids(task)
        if not asset_ids:
            raise AppError(ErrorCode.INVALID_PARAMS, "任务暂无可收藏资产")
        await self._assets.set_favorite_for_user(user_id, asset_ids, favorited=favorited)

    async def delete_task(self, task_id: int, user_id: int) -> None:
        task = await self._tasks.get_active_for_user(task_id, user_id)
        if task is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")
        if (
            GatewayTaskStatus(task.status).is_non_terminal
            and task.union_task_id
            and GenerationKind(task.kind) == GenerationKind.VIDEO
        ):
            try:
                await self._gateway.cancel_task(task.union_task_id, str(user_id))
            except Exception as exc:
                logger.warning(
                    "generate.delete.cancel_upstream_failed",
                    task_id=task_id,
                    union_task_id=task.union_task_id,
                    error=str(exc),
                )
        await self._tasks.soft_delete(task.id)

    async def handle_callback(self, payload: GenerateCallbackPayload) -> GenerationCallbackResult:
        task = await self._tasks.get_by_union_task_id(payload.task_id)
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
            return GenerationCallbackResult(task=task, applied=False)

        reason = resolve_gateway_failure_message(payload.error_code, payload.reason)
        result = normalize_gateway_terminal(payload.status, payload.urls, reason)
        task, applied = await self.apply_result(task, result, callback_sent=True)
        if applied:
            logger.info("generate.callback.written", task_id=task.id, status=task.status)
            return GenerationCallbackResult(task=task, applied=True)
        await self._tasks.mark_callback_sent(task.id)
        task = await self._tasks.get_by_id_required(task.id)
        return GenerationCallbackResult(task=task, applied=False)

    async def list_models(self, kind: GenerationKind) -> GenerateModelsResponse:
        async def _compute() -> dict[str, Any]:
            gateway_models = await self._gateway.list_generation_models()
            items: list[GenerateModelItem] = []
            for gateway_model in gateway_models:
                if gateway_model.generation_kind != kind:
                    continue
                capabilities = self._resolve_capability_for_gateway_model(gateway_model, kind)
                if capabilities is None:
                    logger.warning(
                        "generation.capability.unconfigured_model",
                        model_id=gateway_model.id,
                        generation_kind=kind.value,
                    )
                    continue
                items.append(
                    GenerateModelItem(
                        model_id=gateway_model.id,
                        label=gateway_model.id,
                        kind=kind,
                        supports_vision=gateway_model.supports_vision,
                        param_options=assembly.to_param_options(capabilities),
                    )
                )
            payload = GenerateModelsResponse(items=items).model_dump()
            if not isinstance(payload, dict):
                raise AppError(ErrorCode.INTERNAL_ERROR, "模型列表序列化失败")
            return payload

        try:
            raw = await self._model_cache.get_or_compute(
                f"{MODEL_LIST_CACHE_KEY_PREFIX}{kind.value}",
                _compute,
                ttl=settings.GEN_MODEL_LIST_TTL,
                cache_none=False,
            )
        except Exception as exc:
            logger.error("generate.list_models.error", error=str(exc))
            raise AppError(ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE, "模型列表暂时不可用") from exc
        return GenerateModelsResponse.model_validate(raw)

    async def list_tts_voices(self, model_id: str) -> list[GatewayVoiceItem]:
        response = await self._gateway.list_voices(model_id)
        return list(response.data)

    async def resolve_tts_voice_id(
        self,
        model_id: str,
        *,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
    ) -> str:
        explicit = (voice_id or fallback_voice_id or "").strip()
        if explicit:
            return explicit
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "语音生成必须显式选择音色",
            {"model_id": model_id},
        )

    def get_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind | None = None,
    ) -> GenerationModelCapabilities | None:
        capabilities = self._capabilities.get(model_id)
        if capabilities is None:
            return None
        if kind is not None and capabilities.kind != kind:
            return None
        return capabilities

    async def require_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities:
        capabilities = self.get_model_capabilities(model_id, kind)
        if capabilities is not None:
            return capabilities
        models = await self._gateway.list_generation_models()
        gateway_model = next((item for item in models if item.id == model_id), None)
        logger.warning(
            "generation.capability.missing",
            model_id=model_id,
            requested_kind=kind.value,
            gateway_model_present=gateway_model is not None,
        )
        raise AppError(
            ErrorCode.GENERATION_MODEL_CAPABILITY_UNAVAILABLE,
            "模型能力信息不可用，请刷新模型列表或补充能力配置",
            {"model_id": model_id},
        )

    async def apply_result(
        self,
        task: GenerateTask,
        result: GenerationTerminal,
        *,
        callback_sent: bool,
    ) -> tuple[GenerateTask, bool]:
        current = GatewayTaskStatus(task.status)
        if current.is_terminal:
            return task, False
        if result.status not in TERMINAL_GATEWAY_TASK_STATUSES and result.status < current:
            return task, False

        async with in_transaction():
            updated = await self._tasks.update_if_status(
                task.id,
                expected_status=current,
                fields=result.update_fields(callback_sent=callback_sent),
            )
            if updated != 1:
                return await self._tasks.get_by_id_required(task.id), False
            applied_task = await self._tasks.get_by_id_required(task.id)
            if result.status == GatewayTaskStatus.SUCCEEDED:
                applied_task = await self.ensure_result_assets(applied_task)
        return applied_task, True

    async def ensure_result_assets(self, task: GenerateTask) -> GenerateTask:
        if not task.result_keys or not isinstance(task.result_keys, list):
            return task
        if isinstance(task.result_asset_ids, list) and task.result_asset_ids:
            return task

        kind = GenerationKind(task.kind)
        if kind == GenerationKind.VIDEO:
            asset_type = ASSET_TYPE_VIDEO
        elif kind == GenerationKind.AUDIO:
            asset_type = ASSET_TYPE_AUDIO
        else:
            asset_type = ASSET_TYPE_IMAGE
        mime_type = RESULT_MIME_BY_KIND[kind]

        asset_ids: list[int] = []
        for index, raw_item in enumerate(task.result_keys):
            item = GatewayResultItem.model_validate(raw_item)
            storage_key = item.url
            filename = storage_key.rsplit("/", 1)[-1] or f"{task.kind}-{task.id}-{index}"
            asset = await self._asset_service.create_asset(
                user_id=task.user_id,
                storage_key=storage_key,
                filename=filename,
                mime_type=mime_type,
                asset_type=asset_type,
                source_type=ASSET_SOURCE_GENERATE_RESULT,
                source_id=str(task.id),
                project_id=None,
                metadata={
                    RESULT_ASSET_META_TASK_ID: task.id,
                    RESULT_ASSET_META_UNION_TASK_ID: task.union_task_id,
                    RESULT_ASSET_META_KIND: task.kind,
                    RESULT_ASSET_META_PROMPT: task.prompt,
                    RESULT_ASSET_META_MODEL_ID: task.model_id,
                    RESULT_ASSET_META_RATIO: task.ratio,
                    RESULT_ASSET_META_RESOLUTION: task.resolution,
                    RESULT_ASSET_META_DURATION: task.duration,
                    RESULT_ASSET_META_INDEX: index,
                    RESULT_ASSET_META_GATEWAY_RESULT: item.model_dump(mode="json", exclude_none=False),
                },
            )
            asset_ids.append(asset.id)
        if asset_ids:
            return await self._tasks.set_result_asset_ids(task.id, asset_ids)
        return task

    @staticmethod
    def _observation_for(
        task: GenerateTask,
        observations: GatewayObservationBatch,
    ) -> GatewayQueueObservation | None:
        if task.union_task_id is None:
            return None
        return observations.for_union_task(task.union_task_id)

    async def _observe_non_terminal_tasks(
        self,
        tasks: list[GenerateTask],
    ) -> tuple[list[GenerateTask], GatewayObservationBatch]:
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
            result = normalize_gateway_terminal(gateway_status, None, None)
            updated_task, applied = await self.apply_result(task, result, callback_sent=False)
        if applied:
            logger.info(
                "generate.status_observe.written",
                task_id=updated_task.id,
                status=updated_task.status,
            )
        return updated_task

    async def _apply_gateway_terminal_observation(
        self,
        task: GenerateTask,
    ) -> tuple[GenerateTask, bool]:
        if task.union_task_id is None:
            return task, False
        try:
            response = await self._gateway.get_task(task.union_task_id)
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
            reason = resolve_gateway_failure_message(data.error_code, data.reason)
            result = normalize_gateway_terminal(data.status, data.urls, reason)
        except AppError as exc:
            logger.warning(
                "generate.status_observe.invalid_terminal_payload",
                task_id=task.id,
                union_task_id=task.union_task_id,
                gateway_status=data.status,
                error=str(exc),
            )
            return task, False
        return await self.apply_result(task, result, callback_sent=True)

    async def _fetch_observations(self, union_task_ids: list[int]) -> GatewayObservationBatch:
        unique_ids = list(dict.fromkeys(union_task_ids))
        if not unique_ids:
            return GatewayObservationBatch.empty()
        try:
            response = await self._gateway.get_tasks_queue(unique_ids)
            return GatewayObservationBatch.from_queue_items(response.data.tasks)
        except Exception as exc:
            logger.warning(
                "generate.status_observe.queue_error",
                gateway_task_ids=unique_ids,
                error=str(exc),
            )
            return GatewayObservationBatch.empty()

    async def _load_reference_materials(
        self,
        user_id: int,
        req: SubmitGenerateRequest,
        capabilities: GenerationModelCapabilities,
    ) -> list[GatewayGenerateMaterial]:
        materials: list[GatewayGenerateMaterial] = []
        if req.ref_asset_ids:
            asset_ids = list(dict.fromkeys(item for item in req.ref_asset_ids if item > 0))
            if asset_ids:
                materials.extend(
                    await self._asset_service.assets_to_gateway_materials(
                        user_id=user_id,
                        asset_ids=asset_ids,
                    )
                )
        if req.ref_attachment_ids:
            requested_ids = list(dict.fromkeys(item for item in req.ref_attachment_ids if item > 0))
            if requested_ids:
                rows = await self._attachments.get_by_ids_for_user(requested_ids, user_id)
                by_id = {row.id: row for row in rows}
                missing_ids = [item_id for item_id in requested_ids if item_id not in by_id]
                if missing_ids:
                    raise AppError(ErrorCode.INVALID_PARAMS, "引用素材不存在或无权访问")
                for item_id in requested_ids:
                    row = by_id[item_id]
                    materials.append(
                        GatewayGenerateMaterial(
                            type=material_type_from_mime(row.mime_type),
                            storage_key=row.storage_key,
                        )
                    )
        materials = dedupe_materials(materials)
        validate_reference_materials(
            kind=req.kind,
            reference_mode=req.reference_mode,
            material_types=[MaterialType(item.type) for item in materials],
            capabilities=capabilities,
        )
        return materials

    async def _submit_to_gateway(
        self,
        req: SubmitGenerateRequest,
        materials: list[GatewayGenerateMaterial],
        *,
        voice_id: str | None = None,
    ) -> int:
        rid = uuid4().hex
        callback_url = settings.GENERATE_CALLBACK_URL
        if req.kind == GenerationKind.IMAGE:
            resp = await self._gateway.submit_image(
                build_image_submit_request(req, materials, callback_url=callback_url),
                rid,
            )
        elif req.kind == GenerationKind.AUDIO:
            resolved_voice_id = voice_id or await self.resolve_tts_voice_id(
                req.model_id,
                voice_id=req.voice_id,
            )
            resp = await self._gateway.submit_tts(
                build_tts_submit_request(req, voice_id=resolved_voice_id, callback_url=callback_url),
                rid,
            )
        else:
            resp = await self._gateway.submit_video(
                build_video_submit_request(req, materials, callback_url=callback_url),
                rid,
            )
        task_id = resp.data.task_id
        if not isinstance(task_id, int):
            raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "网关提交响应缺少任务 ID")
        return task_id

    async def _load_reference_materials_by_task_id(
        self,
        tasks: list[GenerateTask],
        user_id: int,
    ) -> dict[int, list[GenerateRefMaterial]]:
        attachment_ids, asset_ids = assembly.collect_reference_ids(tasks)
        if not attachment_ids and not asset_ids:
            return {}
        attachments = await self._attachments.get_by_ids_for_user(attachment_ids, user_id)
        assets = await self._assets.get_active_by_ids_for_user(asset_ids, user_id)
        attachment_by_id = {row.id: row for row in attachments}
        asset_by_id = {row.id: row for row in assets}
        result: dict[int, list[GenerateRefMaterial]] = {}
        for task in tasks:
            materials: list[GenerateRefMaterial] = []
            for attachment_id in task.ref_attachment_ids or []:
                attachment = attachment_by_id.get(attachment_id)
                if attachment is None:
                    continue
                materials.append(
                    assembly.ref_material_from_attachment(
                        attachment_id=attachment.id,
                        asset_id=attachment.asset_id,
                        filename=attachment.filename,
                        mime_type=attachment.mime_type,
                        url=self._attachment_service.build_preview_url(attachment.storage_key),
                    )
                )
            for asset_id in task.ref_asset_ids or []:
                asset = asset_by_id.get(asset_id)
                if asset is None:
                    continue
                materials.append(
                    assembly.ref_material_from_asset(
                        asset_id=asset.id,
                        filename=asset.filename,
                        mime_type=asset.mime_type,
                        url=self._asset_service.preview_url(asset.storage_key),
                        source_type=asset.source_type,
                    )
                )
            result[task.id] = materials
        return result

    async def _load_favorited_asset_ids(
        self,
        tasks: list[GenerateTask],
        user_id: int,
    ) -> set[int]:
        asset_ids: set[int] = set()
        for task in tasks:
            asset_ids.update(assembly.task_result_asset_ids(task))
        if not asset_ids:
            return set()
        rows = await self._assets.get_favorited_by_ids_for_user(asset_ids, user_id)
        return {row.id for row in rows}

    async def _favorite_task_ids_for_user(self, user_id: int) -> set[int]:
        rows = await self._assets.list_favorited_for_user(user_id)
        task_ids: set[int] = set()
        for row in rows:
            task_id = assembly.asset_task_id(row)
            if task_id is not None:
                task_ids.add(task_id)
        return task_ids

    def _resolve_capability_for_gateway_model(
        self,
        gateway_model: GatewayModelItem,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities | None:
        if gateway_model.generation_kind != kind:
            return None
        return self.get_model_capabilities(gateway_model.id, kind)
