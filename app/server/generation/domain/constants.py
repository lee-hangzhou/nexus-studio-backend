from app.server.generation.domain.enums import (
    GatewayContentType,
    GenerationKind,
    MaterialType,
    ReferenceMode,
)

MATERIAL_MAX_BYTES = 200 * 1024 * 1024

MIME_PREFIX_IMAGE = "image/"
MIME_PREFIX_VIDEO = "video/"
MIME_PREFIX_AUDIO = "audio/"

RESULT_MIME_BY_KIND: dict[GenerationKind, str] = {
    GenerationKind.IMAGE: "image/png",
    GenerationKind.VIDEO: "video/mp4",
    GenerationKind.AUDIO: "audio/mpeg",
}

CONTENT_TYPE_BY_MATERIAL: dict[MaterialType, GatewayContentType] = {
    MaterialType.IMAGE: GatewayContentType.IMAGE,
    MaterialType.VIDEO: GatewayContentType.VIDEO,
    MaterialType.AUDIO: GatewayContentType.AUDIO,
}

MATERIAL_TYPE_LABELS: dict[MaterialType, str] = {
    MaterialType.IMAGE: "图片",
    MaterialType.VIDEO: "视频",
    MaterialType.AUDIO: "音频",
}

REFERENCE_MODE_LABELS: dict[ReferenceMode, str] = {
    ReferenceMode.FIRST_FRAME: "首帧参考",
    ReferenceMode.FIRST_LAST_FRAME: "首尾帧参考",
    ReferenceMode.OMNI_REFERENCE: "全能参考",
    ReferenceMode.VIDEO_EDIT: "视频编辑",
}

MODEL_LIST_CACHE_KEY_PREFIX = "gen:models:"
MODEL_CAPABILITIES_CACHE_KEY = "gen:model_capabilities"

RESULT_ASSET_META_TASK_ID = "task_id"
RESULT_ASSET_META_UNION_TASK_ID = "union_task_id"
RESULT_ASSET_META_KIND = "kind"
RESULT_ASSET_META_PROMPT = "prompt"
RESULT_ASSET_META_MODEL_ID = "model_id"
RESULT_ASSET_META_RATIO = "ratio"
RESULT_ASSET_META_RESOLUTION = "resolution"
RESULT_ASSET_META_DURATION = "duration"
RESULT_ASSET_META_INDEX = "index"
RESULT_ASSET_META_GATEWAY_RESULT = "gateway_result"
