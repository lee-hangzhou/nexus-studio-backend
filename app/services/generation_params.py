from __future__ import annotations

from collections import Counter

from app.contracts.gateway import GatewayContentPart, GatewayGenerateMaterial
from app.domain.generation.enums import GatewayContentType, GenerationKind, MaterialType, ReferenceMode
from app.domain.generation.models import GenerationModelCapabilities
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.schemas.generate import SubmitGenerateRequest

GENERATE_MATERIAL_MAX_BYTES = 200 * 1024 * 1024

CONTENT_TYPE_BY_MATERIAL_TYPE = {
    MaterialType.IMAGE: GatewayContentType.IMAGE,
    MaterialType.VIDEO: GatewayContentType.VIDEO,
    MaterialType.AUDIO: GatewayContentType.AUDIO,
}


def material_type_from_mime(mime_type: str) -> MaterialType:
    if mime_type.startswith("image/"):
        return MaterialType.IMAGE
    if mime_type.startswith("video/"):
        return MaterialType.VIDEO
    if mime_type.startswith("audio/"):
        return MaterialType.AUDIO
    raise AppError(ErrorCode.INVALID_PARAMS, "仅支持引用图片、视频或音频素材")


def validate_material_upload(mime_type: str, size: int) -> None:
    if size <= 0:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件为空")
    if size > GENERATE_MATERIAL_MAX_BYTES:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件过大")
    material_type_from_mime(mime_type)


def validate_request_params(
    req: SubmitGenerateRequest,
    capabilities: GenerationModelCapabilities,
) -> None:
    if capabilities.model_id != req.model_id or capabilities.kind != req.kind:
        raise AppError(
            ErrorCode.GENERATION_MODEL_CAPABILITY_UNAVAILABLE,
            "模型能力与生成请求不匹配",
        )

    if req.kind == GenerationKind.AUDIO:
        return

    allowed_ratios = capabilities.ratios_for_resolution(req.resolution)
    if req.ratio is not None and allowed_ratios and req.ratio not in allowed_ratios:
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持宽高比 {req.ratio}")
    if (
        req.resolution is not None
        and capabilities.resolutions
        and req.resolution not in capabilities.resolutions
    ):
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持分辨率 {req.resolution}")

    if req.kind == GenerationKind.IMAGE:
        if capabilities.counts and req.count not in capabilities.counts:
            raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持生成 {req.count} 张图片")
        return

    if req.duration is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须指定时长")
    if capabilities.durations and req.duration not in capabilities.durations:
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持 {req.duration} 秒视频")
    if (
        req.reference_mode is not None
        and capabilities.reference_modes
        and req.reference_mode not in capabilities.reference_modes
    ):
        raise AppError(ErrorCode.INVALID_PARAMS, "当前模型不支持所选参考模式")


def validate_reference_materials(
    req: SubmitGenerateRequest,
    materials: list[GatewayGenerateMaterial],
    capabilities: GenerationModelCapabilities,
) -> None:
    limits = capabilities.material_limits
    counts = Counter(MaterialType(item.type) for item in materials)
    image_count = counts[MaterialType.IMAGE]
    video_count = counts[MaterialType.VIDEO]
    audio_count = counts[MaterialType.AUDIO]

    if not materials:
        if limits.requires_any:
            raise AppError(ErrorCode.INVALID_PARAMS, "当前模型需要先 @ 引用素材")
        if req.kind == GenerationKind.VIDEO and _reference_mode_requires_material(req.reference_mode):
            raise AppError(ErrorCode.INVALID_PARAMS, "当前视频模式需要先 @ 引用素材")
        return

    for material_type, count in counts.items():
        limit = limits.limit_for(material_type)
        if count > limit:
            label = {
                MaterialType.IMAGE: "图片",
                MaterialType.VIDEO: "视频",
                MaterialType.AUDIO: "音频",
            }[material_type]
            raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型最多引用 {limit} 个{label}素材")

    if req.kind == GenerationKind.IMAGE:
        if video_count or audio_count:
            raise AppError(ErrorCode.INVALID_PARAMS, "图片生成仅支持引用图片素材")
        return

    reference_mode = req.reference_mode
    if reference_mode is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须显式选择参考模式")
    if reference_mode == ReferenceMode.FIRST_FRAME:
        if image_count != 1 or video_count or audio_count:
            raise AppError(ErrorCode.INVALID_PARAMS, "首帧参考模式需要且仅需要 1 张图片")
    elif reference_mode == ReferenceMode.FIRST_LAST_FRAME:
        if image_count != 2 or video_count or audio_count:
            raise AppError(ErrorCode.INVALID_PARAMS, "首尾帧参考模式需要且仅需要 2 张图片")
    elif reference_mode == ReferenceMode.OMNI_REFERENCE:
        if audio_count and not image_count and not video_count and not limits.allow_audio_only:
            raise AppError(ErrorCode.INVALID_PARAMS, "全能参考不能只引用音频素材")
    elif reference_mode == ReferenceMode.VIDEO_EDIT:
        if video_count != 1 or audio_count:
            raise AppError(ErrorCode.INVALID_PARAMS, "视频编辑模式需要且仅需要 1 段视频，可额外引用图片")


def content_with_materials(
    prompt: str,
    materials: list[GatewayGenerateMaterial],
) -> list[GatewayContentPart]:
    content = [
        GatewayContentPart(
            type=GatewayContentType.TEXT,
            text=prompt,
        )
    ]
    for index, material in enumerate(materials, start=1):
        content.append(
            GatewayContentPart(
                type=CONTENT_TYPE_BY_MATERIAL_TYPE[MaterialType(material.type)],
                material_ref_index=index,
            )
        )
    return content


def _reference_mode_requires_material(reference_mode: ReferenceMode | None) -> bool:
    return reference_mode in {
        ReferenceMode.FIRST_FRAME,
        ReferenceMode.FIRST_LAST_FRAME,
        ReferenceMode.VIDEO_EDIT,
    }
