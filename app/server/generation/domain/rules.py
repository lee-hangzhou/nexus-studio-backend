from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.constants import (
    MATERIAL_MAX_BYTES,
    MATERIAL_TYPE_LABELS,
    MIME_PREFIX_AUDIO,
    MIME_PREFIX_IMAGE,
    MIME_PREFIX_VIDEO,
)
from app.server.generation.domain.enums import GenerationKind, MaterialType, ReferenceMode
from app.server.generation.domain.models import GenerationModelCapabilities


def material_type_from_mime(mime_type: str) -> MaterialType:
    if mime_type.startswith(MIME_PREFIX_IMAGE):
        return MaterialType.IMAGE
    if mime_type.startswith(MIME_PREFIX_VIDEO):
        return MaterialType.VIDEO
    if mime_type.startswith(MIME_PREFIX_AUDIO):
        return MaterialType.AUDIO
    raise AppError(ErrorCode.INVALID_PARAMS, "仅支持引用图片、视频或音频素材")


def validate_material_upload(*, mime_type: str, size: int) -> MaterialType:
    if size <= 0:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件为空")
    if size > MATERIAL_MAX_BYTES:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件过大")
    return material_type_from_mime(mime_type)


def reference_mode_requires_material(reference_mode: ReferenceMode | None) -> bool:
    return reference_mode in {
        ReferenceMode.FIRST_FRAME,
        ReferenceMode.FIRST_LAST_FRAME,
        ReferenceMode.VIDEO_EDIT,
    }


def validate_submit_params(
    *,
    kind: GenerationKind,
    model_id: str,
    capabilities: GenerationModelCapabilities,
    ratio: str | None,
    resolution: str | None,
    count: int,
    duration: int | None,
    reference_mode: ReferenceMode | None,
) -> None:
    if capabilities.model_id != model_id or capabilities.kind != kind:
        raise AppError(
            ErrorCode.GENERATION_MODEL_CAPABILITY_UNAVAILABLE,
            "模型能力与生成请求不匹配",
        )
    if kind == GenerationKind.AUDIO:
        return
    allowed_ratios = capabilities.ratios_for_resolution(resolution)
    if ratio is not None and allowed_ratios and ratio not in allowed_ratios:
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持宽高比 {ratio}")
    if resolution is not None and capabilities.resolutions and resolution not in capabilities.resolutions:
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持分辨率 {resolution}")
    if kind == GenerationKind.IMAGE:
        if capabilities.counts and count not in capabilities.counts:
            raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持生成 {count} 张图片")
        return
    if duration is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "视频生成必须指定时长")
    if capabilities.durations and duration not in capabilities.durations:
        raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型不支持 {duration} 秒视频")
    if (
        reference_mode is not None
        and capabilities.reference_modes
        and reference_mode not in capabilities.reference_modes
    ):
        raise AppError(ErrorCode.INVALID_PARAMS, "当前模型不支持所选参考模式")


def validate_reference_materials(
    *,
    kind: GenerationKind,
    reference_mode: ReferenceMode | None,
    material_types: Sequence[MaterialType],
    capabilities: GenerationModelCapabilities,
) -> None:
    limits = capabilities.material_limits
    counts = Counter(material_types)
    image_count = counts[MaterialType.IMAGE]
    video_count = counts[MaterialType.VIDEO]
    audio_count = counts[MaterialType.AUDIO]
    if not material_types:
        if limits.requires_any:
            raise AppError(ErrorCode.INVALID_PARAMS, "当前模型需要先 @ 引用素材")
        if kind == GenerationKind.VIDEO and reference_mode_requires_material(reference_mode):
            raise AppError(ErrorCode.INVALID_PARAMS, "当前视频模式需要先 @ 引用素材")
        return
    for material_type, count in counts.items():
        limit = limits.limit_for(material_type)
        if count > limit:
            label = MATERIAL_TYPE_LABELS[material_type]
            raise AppError(ErrorCode.INVALID_PARAMS, f"当前模型最多引用 {limit} 个{label}素材")
    if kind == GenerationKind.IMAGE:
        if video_count or audio_count:
            raise AppError(ErrorCode.INVALID_PARAMS, "图片生成仅支持引用图片素材")
        return
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
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "视频编辑模式需要且仅需要 1 段视频，可额外引用图片",
            )
