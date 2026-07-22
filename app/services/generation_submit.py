from __future__ import annotations

from typing import cast
from uuid import uuid4

from app.assets.service import asset_service
from app.contracts.gateway import (
    GatewayGenerateMaterial,
    GatewayImageSubmitRequest,
    GatewayTTSSubmitRequest,
    GatewayVideoSubmitRequest,
)
from app.core.config import settings
from app.core.gateway import gateway_client
from app.core.logger import logger
from app.domain.enums import TERMINAL_GATEWAY_TASK_STATUSES, GatewayTaskStatus
from app.domain.generation.enums import GenerationKind
from app.domain.generation.models import GenerationModelCapabilities
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.chat_attachments import ChatAttachments
from app.models.generate_task import GenerateTask
from app.schemas.generate import GenerateTaskSubmitResponse, SubmitGenerateRequest
from app.services.generation_capabilities import generation_capabilities
from app.services.generation_params import (
    content_with_materials,
    material_type_from_mime,
    validate_reference_materials,
    validate_request_params,
)
from app.services.generation_voices import resolve_tts_voice_id


def _dedupe_gateway_materials(
    materials: list[GatewayGenerateMaterial],
) -> list[GatewayGenerateMaterial]:
    seen: set[str] = set()
    deduped: list[GatewayGenerateMaterial] = []
    for item in materials:
        key = str(item.storage_key or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


async def load_reference_materials(
    user_id: int,
    req: SubmitGenerateRequest,
    capabilities: GenerationModelCapabilities,
) -> list[GatewayGenerateMaterial]:
    materials: list[GatewayGenerateMaterial] = []

    if req.ref_asset_ids:
        asset_ids = list(dict.fromkeys(int(item) for item in req.ref_asset_ids if int(item) > 0))
        if asset_ids:
            materials.extend(
                cast(
                    list[GatewayGenerateMaterial],
                    await asset_service.assets_to_gateway_materials(
                        user_id=user_id,
                        asset_ids=asset_ids,
                    ),
                )
            )

    if req.ref_attachment_ids:
        requested_ids = list(dict.fromkeys(int(item) for item in req.ref_attachment_ids if int(item) > 0))
        if requested_ids:
            rows = await ChatAttachments.filter(user_id=user_id, id__in=requested_ids)
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

    materials = _dedupe_gateway_materials(materials)
    validate_reference_materials(req, materials, capabilities)
    return materials


async def submit_to_gateway(
    req: SubmitGenerateRequest,
    materials: list[GatewayGenerateMaterial],
    capabilities: GenerationModelCapabilities,
) -> int:
    rid = uuid4().hex
    if req.kind == GenerationKind.IMAGE:
        image_payload = GatewayImageSubmitRequest(
            model=req.model_id,
            content=content_with_materials(req.prompt, materials),
            max_images=req.count,
            callback_url=settings.GENERATE_CALLBACK_URL,
            materials=materials,
            ratio=req.ratio,
            resolution=req.resolution,
        )
        resp = await gateway_client.submit_image(image_payload, rid)
    elif req.kind == GenerationKind.AUDIO:
        voice_id = await resolve_tts_voice_id(req.model_id, voice_id=req.voice_id)
        tts_payload = GatewayTTSSubmitRequest(
            model=req.model_id,
            text=req.prompt,
            voice_id=voice_id,
            callback_url=settings.GENERATE_CALLBACK_URL,
        )
        resp = await gateway_client.submit_tts(tts_payload, rid)
    else:
        reference_mode = req.reference_mode
        duration = req.duration
        if reference_mode is None or duration is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须显式选择参考模式和时长")
        video_payload = GatewayVideoSubmitRequest(
            model=req.model_id,
            content=content_with_materials(req.prompt, materials),
            callback_url=settings.GENERATE_CALLBACK_URL,
            reference_mode=reference_mode,
            duration=duration,
            materials=materials,
            ratio=req.ratio,
            resolution=req.resolution,
        )
        resp = await gateway_client.submit_video(video_payload, rid)

    return cast(int, resp.data.task_id)


async def submit_generate_task(user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
    capabilities = await generation_capabilities.require(req.model_id, req.kind)
    validate_request_params(req, capabilities)
    materials: list[GatewayGenerateMaterial] = []
    if req.kind != GenerationKind.AUDIO:
        materials = await load_reference_materials(user_id, req, capabilities)

    voice_id: str | None = None
    if req.kind == GenerationKind.AUDIO:
        voice_id = await resolve_tts_voice_id(req.model_id, voice_id=req.voice_id)

    reference_mode = None
    if req.kind == GenerationKind.VIDEO:
        reference_mode = req.reference_mode
        if reference_mode is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须显式选择参考模式")

    task = await GenerateTask.create(
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
        union_task_id = await submit_to_gateway(req, materials, capabilities)
    except Exception as exc:
        logger.error(
            "generate.submit.gateway_error",
            task_id=task.id,
            error=str(exc),
        )
        await GenerateTask.filter(
            id=task.id,
            status__not_in=[int(status) for status in TERMINAL_GATEWAY_TASK_STATUSES],
        ).update(status=GatewayTaskStatus.FAILED, error_message=str(exc))
        raise AppError(
            ErrorCode.GATEWAY_SUBMIT_ERROR,
            "提交生成任务失败：上游服务暂不可用",
            {"task_id": task.id},
        ) from exc

    queued = await GenerateTask.filter(
        id=task.id,
        status=GatewayTaskStatus.CREATED,
    ).update(
        union_task_id=union_task_id,
        status=GatewayTaskStatus.QUEUED,
    )
    if queued != 1:
        await GenerateTask.filter(id=task.id).update(union_task_id=union_task_id)
    task = await GenerateTask.get(id=task.id)
    logger.info(
        "generate.submit.ok",
        task_id=task.id,
        union_task_id=union_task_id,
        status=int(task.status),
    )
    return GenerateTaskSubmitResponse(task_id=task.id, status=task.status)
