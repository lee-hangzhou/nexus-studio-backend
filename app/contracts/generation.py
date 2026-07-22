from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.server.generation.domain.enums import GenerationKind, ReferenceMode
from app.server.generation.domain.models import GenerationModelCapabilities, MaterialLimits


class GenerationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class MaterialLimitsConfig(GenerationContract):
    images: int = Field(default=0, ge=0)
    videos: int = Field(default=0, ge=0)
    audios: int = Field(default=0, ge=0)
    requires_any: bool = False
    allow_audio_only: bool = False


class GenerationModelCapabilitiesConfig(GenerationContract):
    model_id: str = Field(min_length=1)
    kind: GenerationKind
    ratios: list[str] = Field(default_factory=list)
    resolutions: list[str] = Field(default_factory=list)
    counts: list[int] = Field(default_factory=list)
    durations: list[int] = Field(default_factory=list)
    reference_modes: list[ReferenceMode] = Field(default_factory=list)
    material_limits: MaterialLimitsConfig = Field(default_factory=MaterialLimitsConfig)
    ratios_by_resolution: dict[str, list[str]] = Field(default_factory=dict)

    def to_domain(self) -> GenerationModelCapabilities:
        return GenerationModelCapabilities(
            model_id=self.model_id,
            kind=GenerationKind(self.kind),
            ratios=tuple(self.ratios),
            resolutions=tuple(self.resolutions),
            counts=tuple(self.counts),
            durations=tuple(self.durations),
            reference_modes=tuple(ReferenceMode(item) for item in self.reference_modes),
            material_limits=MaterialLimits(**self.material_limits.model_dump()),
            ratios_by_resolution=tuple(
                (resolution, tuple(ratios))
                for resolution, ratios in self.ratios_by_resolution.items()
            ),
        )


class ReferenceModeOption(GenerationContract):
    value: ReferenceMode
    label: str


class GenerateParamOptions(GenerationContract):
    ratios: list[str] = Field(default_factory=list)
    resolutions: list[str] = Field(default_factory=list)
    counts: list[int] = Field(default_factory=list)
    durations: list[int] = Field(default_factory=list)
    reference_modes: list[ReferenceModeOption] = Field(default_factory=list)
    ratios_by_resolution: dict[str, list[str]] = Field(default_factory=dict)
