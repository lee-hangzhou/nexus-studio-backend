from __future__ import annotations

import time
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app.server.generation.domain.enums import GenerationKind, ReferenceMode
from app.server.generation.domain.models import GenerationModelCapabilities, MaterialLimits


class CachedMaterialLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: StrictInt = 0
    videos: StrictInt = 0
    audios: StrictInt = 0
    requires_any: StrictBool = False
    allow_audio_only: StrictBool = False


class CachedModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: StrictStr
    kind: GenerationKind
    ratios: list[StrictStr] = Field(default_factory=list)
    resolutions: list[StrictStr] = Field(default_factory=list)
    counts: list[StrictInt] = Field(default_factory=list)
    durations: list[StrictInt] = Field(default_factory=list)
    reference_modes: list[ReferenceMode] = Field(default_factory=list)
    material_limits: CachedMaterialLimits = Field(default_factory=CachedMaterialLimits)
    ratios_by_resolution: dict[str, list[StrictStr]] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, capabilities: GenerationModelCapabilities) -> Self:
        limits = capabilities.material_limits
        return cls(
            model_id=capabilities.model_id,
            kind=capabilities.kind,
            ratios=list(capabilities.ratios),
            resolutions=list(capabilities.resolutions),
            counts=list(capabilities.counts),
            durations=list(capabilities.durations),
            reference_modes=list(capabilities.reference_modes),
            material_limits=CachedMaterialLimits(
                images=limits.images,
                videos=limits.videos,
                audios=limits.audios,
                requires_any=limits.requires_any,
                allow_audio_only=limits.allow_audio_only,
            ),
            ratios_by_resolution={
                resolution: list(ratios) for resolution, ratios in capabilities.ratios_by_resolution
            },
        )

    def to_domain(self) -> GenerationModelCapabilities:
        limits = self.material_limits
        return GenerationModelCapabilities(
            model_id=self.model_id,
            kind=self.kind,
            ratios=tuple(self.ratios),
            resolutions=tuple(self.resolutions),
            counts=tuple(self.counts),
            durations=tuple(self.durations),
            reference_modes=tuple(self.reference_modes),
            material_limits=MaterialLimits(
                images=limits.images,
                videos=limits.videos,
                audios=limits.audios,
                requires_any=limits.requires_any,
                allow_audio_only=limits.allow_audio_only,
            ),
            ratios_by_resolution=tuple(
                (resolution, tuple(ratios)) for resolution, ratios in self.ratios_by_resolution.items()
            ),
        )


class CachedCapabilitiesPayload(BaseModel):
    """共享 capabilities cache 条目；expires_at 为写入时锚定的绝对过期时刻。"""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, CachedModelCapabilities]
    expires_at: StrictFloat

    @classmethod
    def from_domain_map(
        cls,
        capability_map: dict[str, GenerationModelCapabilities],
        *,
        ttl_seconds: int,
    ) -> Self:
        return cls(
            models={
                model_id: CachedModelCapabilities.from_domain(capabilities)
                for model_id, capabilities in capability_map.items()
            },
            expires_at=time.time() + float(ttl_seconds),
        )

    def to_domain_map(self) -> dict[str, GenerationModelCapabilities]:
        return {model_id: item.to_domain() for model_id, item in self.models.items()}
