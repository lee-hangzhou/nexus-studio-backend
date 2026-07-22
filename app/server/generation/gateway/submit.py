from __future__ import annotations

from app.contracts.gateway import (
    GatewayContentPart,
    GatewayGenerateMaterial,
    GatewayImageSubmitRequest,
    GatewayTTSSubmitRequest,
    GatewayVideoSubmitRequest,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.constants import CONTENT_TYPE_BY_MATERIAL
from app.server.generation.domain.enums import GatewayContentType, GenerationKind, MaterialType, ReferenceMode
from app.server.generation.schemas import SubmitGenerateRequest


def dedupe_materials(
    materials: list[GatewayGenerateMaterial],
) -> list[GatewayGenerateMaterial]:
    seen: set[str] = set()
    deduped: list[GatewayGenerateMaterial] = []
    for item in materials:
        key = item.storage_key or ""
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def content_with_materials(
    prompt: str,
    materials: list[GatewayGenerateMaterial],
) -> list[GatewayContentPart]:
    content = [GatewayContentPart(type=GatewayContentType.TEXT, text=prompt)]
    for index, material in enumerate(materials, start=1):
        content.append(
            GatewayContentPart(
                type=CONTENT_TYPE_BY_MATERIAL[MaterialType(material.type)],
                material_ref_index=index,
            )
        )
    return content


def build_image_submit_request(
    req: SubmitGenerateRequest,
    materials: list[GatewayGenerateMaterial],
    *,
    callback_url: str,
) -> GatewayImageSubmitRequest:
    return GatewayImageSubmitRequest(
        model=req.model_id,
        content=content_with_materials(req.prompt, materials),
        max_images=req.count,
        callback_url=callback_url,
        materials=materials,
        ratio=req.ratio,
        resolution=req.resolution,
    )


def build_video_submit_request(
    req: SubmitGenerateRequest,
    materials: list[GatewayGenerateMaterial],
    *,
    callback_url: str,
) -> GatewayVideoSubmitRequest:
    if req.reference_mode is None or req.duration is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须显式选择参考模式和时长")
    return GatewayVideoSubmitRequest(
        model=req.model_id,
        content=content_with_materials(req.prompt, materials),
        callback_url=callback_url,
        reference_mode=req.reference_mode,
        duration=req.duration,
        materials=materials,
        ratio=req.ratio,
        resolution=req.resolution,
    )


def build_tts_submit_request(
    req: SubmitGenerateRequest,
    *,
    voice_id: str,
    callback_url: str,
) -> GatewayTTSSubmitRequest:
    return GatewayTTSSubmitRequest(
        model=req.model_id,
        text=req.prompt,
        voice_id=voice_id,
        callback_url=callback_url,
    )


def require_video_reference_mode(req: SubmitGenerateRequest) -> ReferenceMode:
    if req.kind != GenerationKind.VIDEO:
        raise AppError(ErrorCode.INVALID_PARAMS, "非视频请求无需参考模式")
    if req.reference_mode is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须显式选择参考模式")
    return req.reference_mode
