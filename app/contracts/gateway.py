from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, StrictStr

from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.domain.enums import (
    GatewayContentType,
    GatewayModelTaskType,
    GenerationKind,
    MaterialType,
    ReferenceMode,
)


class GatewayContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class GatewayResponseData(BaseModel):
    """Typed payload returned by the gateway."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class GatewayEnvelopeResponse(BaseModel):
    """union_lm 任务类 API 的标准响应信封 {code, message, data}。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )

    code: StrictInt
    message: StrictStr


class GatewayMaterialLimits(BaseModel):
    """网关公开目录 parameters.material_limits。"""

    model_config = ConfigDict(extra="forbid")

    images: StrictInt = 0
    videos: StrictInt = 0
    audios: StrictInt = 0
    requires_any: StrictBool = False
    allow_audio_only: StrictBool = False


class GatewayModelParameters(BaseModel):
    """网关 GET /api/v1/models 厚目录 parameters。"""

    model_config = ConfigDict(extra="forbid")

    resolutions: list[StrictStr] = Field(default_factory=list)
    ratios: list[StrictStr] = Field(default_factory=list)
    ratios_by_resolution: dict[str, list[StrictStr]] = Field(default_factory=dict)
    counts: list[StrictInt] = Field(default_factory=list)
    durations: list[StrictInt] = Field(default_factory=list)
    reference_modes: list[StrictInt] = Field(default_factory=list)
    material_limits: GatewayMaterialLimits = Field(default_factory=GatewayMaterialLimits)
    input_schema: JsonValue | None = None


class GatewayModelItem(BaseModel):
    """Strict OpenAI-compatible model catalog item（厚目录）。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: StrictStr = Field(min_length=1)
    object: StrictStr
    task_type: GatewayModelTaskType
    supports_vision: StrictBool
    parameters: GatewayModelParameters

    @property
    def generation_kind(self) -> GenerationKind | None:
        if self.task_type == GatewayModelTaskType.IMAGE:
            return GenerationKind.IMAGE
        if self.task_type == GatewayModelTaskType.VIDEO:
            return GenerationKind.VIDEO
        if self.task_type == GatewayModelTaskType.TTS:
            return GenerationKind.AUDIO
        return None


class GatewayModelsResponse(BaseModel):
    """OpenAI /v1/models 兼容列表信封。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    data: list[GatewayModelItem]
    object: str


class GatewayGenerateMaterial(GatewayContract):
    type: MaterialType
    storage_key: str = Field(serialization_alias="tosKey", min_length=1)


class GatewayContentPart(GatewayContract):
    type: GatewayContentType
    text: str | None = None
    material_ref_index: int | None = Field(
        default=None,
        serialization_alias="materialRefIndex",
        ge=1,
    )


class GatewayImageSubmitRequest(GatewayContract):
    model: str
    content: list[GatewayContentPart]
    max_images: int = Field(serialization_alias="maxImages", ge=1)
    callback_url: str = Field(serialization_alias="callbackUrl")
    materials: list[GatewayGenerateMaterial] = Field(default_factory=list)
    ratio: str | None = None
    resolution: str | None = None


class GatewayVideoSubmitRequest(GatewayContract):
    model: str
    content: list[GatewayContentPart]
    callback_url: str = Field(serialization_alias="callbackUrl")
    reference_mode: ReferenceMode = Field(serialization_alias="referenceMode")
    duration: int
    materials: list[GatewayGenerateMaterial] = Field(default_factory=list)
    ratio: str | None = None
    resolution: str | None = None


class GatewayTTSAudioOutput(GatewayContract):
    format: str | None = None
    sample_rate: int | None = Field(default=None, serialization_alias="sampleRate")
    bitrate: int | None = None
    channel: int | None = None


class GatewayTTSSubmitRequest(GatewayContract):
    model: str
    text: str = Field(min_length=1)
    voice_id: str = Field(serialization_alias="voiceId", min_length=1)
    callback_url: str = Field(serialization_alias="callbackUrl")
    speed: float | None = None
    volume: float | None = None
    pitch: int | None = None
    audio: GatewayTTSAudioOutput | None = None


class GatewayVoiceItem(GatewayResponseData):
    voice_id: StrictStr = Field(alias="voiceId", min_length=1)
    name: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)


class GatewayListVoicesResponse(GatewayResponseData):
    object: str
    model: str
    data: list[GatewayVoiceItem]


class GatewayTaskSubmitData(GatewayResponseData):
    task_id: StrictInt = Field(alias="taskId", ge=1)


class GatewayTaskSubmitResponse(GatewayEnvelopeResponse):
    data: GatewayTaskSubmitData


class GatewayResultItem(GatewayResponseData):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: StrictStr = Field(min_length=1)
    type: StrictInt | None = None
    width: StrictInt | None = None
    height: StrictInt | None = None
    duration: StrictFloat | StrictInt | None = None
    ratio: StrictStr | None = None
    resolution: StrictStr | None = None
    frames_per_second: StrictInt | None = Field(default=None, alias="framesPerSecond")


class GatewayTaskStatusData(GatewayResponseData):
    """对齐 union_lm api.TaskResponse。"""

    task_id: StrictInt = Field(alias="taskId", ge=1)
    status: GatewayTaskStatus
    urls: list[GatewayResultItem] | None = None
    reason: str | None = None
    error_code: StrictInt | None = Field(default=None, alias="errorCode")
    result: JsonValue | None = None


class GatewayTaskStatusResponse(GatewayEnvelopeResponse):
    data: GatewayTaskStatusData


class GatewayQueueItem(GatewayResponseData):
    task_id: StrictInt = Field(alias="taskId", ge=1)
    status: StrictInt
    position: StrictInt | None = None
    total: StrictInt | None = None
    estimated_wait_seconds: StrictInt | None = Field(default=None, alias="estimatedWaitSeconds")


class GatewayQueueData(GatewayResponseData):
    tasks: list[GatewayQueueItem]


class GatewayQueueResponse(GatewayEnvelopeResponse):
    data: GatewayQueueData


class GatewayGenerateCallback(GatewayResponseData):
    """对齐 union_lm api.TaskResponse（callback 直 POST，无外层信封）。"""

    task_id: StrictInt = Field(alias="taskId", ge=1)
    status: GatewayTaskStatus
    reason: str | None = None
    error_code: StrictInt | None = Field(default=None, alias="errorCode")
    urls: list[GatewayResultItem] | None = None
    result: JsonValue | None = None


def safe_gateway_response_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    return {
        "keys": sorted(str(key) for key in payload)[:20],
        "data_type": type(payload.get("data")).__name__,
    }
