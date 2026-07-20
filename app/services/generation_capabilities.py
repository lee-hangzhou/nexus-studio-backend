from __future__ import annotations

from typing import cast

from app.contracts.gateway import GatewayModelItem
from app.contracts.generation import GenerateParamOptions, ReferenceModeOption
from app.core.config import settings
from app.core.gateway import gateway_client
from app.core.logger import logger
from app.domain.generation.enums import GenerationKind, ReferenceMode
from app.domain.generation.models import GenerationModelCapabilities, MaterialLimits
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode

REFERENCE_MODE_LABELS = {
    ReferenceMode.FIRST_FRAME: "首帧参考",
    ReferenceMode.FIRST_LAST_FRAME: "首尾帧参考",
    ReferenceMode.OMNI_REFERENCE: "全能参考",
    ReferenceMode.VIDEO_EDIT: "视频编辑",
}


class GenerationCapabilitiesRegistry:
    def __init__(self) -> None:
        self._items: dict[str, GenerationModelCapabilities] = {
            config.model_id: config.to_domain()
            for config in settings.GENERATION_MODEL_CAPABILITIES
        }

    @staticmethod
    def audio_gateway_fallback(model_id: str) -> GenerationModelCapabilities:
        """TTS 模型无 .env 能力条目时，用网关 task_type=8 的最小能力占位。"""
        return GenerationModelCapabilities(
            model_id=model_id,
            kind=GenerationKind.AUDIO,
            ratios=(),
            resolutions=(),
            counts=(),
            durations=(),
            reference_modes=(),
            material_limits=MaterialLimits(),
        )

    def get(
        self,
        model_id: str,
        kind: GenerationKind | None = None,
    ) -> GenerationModelCapabilities | None:
        capabilities = cast(
            GenerationModelCapabilities | None,
            self._items.get(model_id),
        )
        if capabilities is None:
            return None
        if kind is not None and capabilities.kind != kind:
            return None
        return capabilities

    def resolve_for_gateway_model(
        self,
        gateway_model: GatewayModelItem,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities | None:
        """列表接口：配置优先，audio 可对网关 TTS 模型 fallback。"""
        capabilities = self.get(gateway_model.id, kind)
        if capabilities is not None:
            return capabilities
        if kind == GenerationKind.AUDIO and gateway_model.generation_kind == GenerationKind.AUDIO:
            return self.audio_gateway_fallback(gateway_model.id)
        return None

    async def require(
        self,
        model_id: str,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities:
        capabilities = self.get(model_id, kind)
        if capabilities is not None:
            return capabilities

        models = await gateway_client.list_generation_models()
        gateway_model = next((item for item in models if item.id == model_id), None)
        if kind == GenerationKind.AUDIO and gateway_model is not None and gateway_model.generation_kind == GenerationKind.AUDIO:
            return self.audio_gateway_fallback(model_id)
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

    def to_param_options(self, capabilities: GenerationModelCapabilities) -> GenerateParamOptions:
        return GenerateParamOptions(
            ratios=list(capabilities.ratios),
            resolutions=list(capabilities.resolutions),
            counts=list(capabilities.counts),
            durations=list(capabilities.durations),
            reference_modes=[
                ReferenceModeOption(
                    value=mode,
                    label=REFERENCE_MODE_LABELS[mode],
                )
                for mode in capabilities.reference_modes
            ],
            ratios_by_resolution={
                resolution: list(ratios)
                for resolution, ratios in capabilities.ratios_by_resolution
            },
        )


generation_capabilities = GenerationCapabilitiesRegistry()
