from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.server.generation.domain.enums import ReferenceMode


class GenerationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ReferenceModeOption(GenerationContract):
    value: ReferenceMode
    label: str


class GenerateMaterialLimits(GenerationContract):
    """产品 list_models.param_options.material_limits，与网关目录同构。"""

    images: StrictInt = 0
    videos: StrictInt = 0
    audios: StrictInt = 0
    requires_any: StrictBool = False
    allow_audio_only: StrictBool = False


class GenerateParamOptions(GenerationContract):
    ratios: list[str] = Field(default_factory=list)
    resolutions: list[str] = Field(default_factory=list)
    counts: list[int] = Field(default_factory=list)
    durations: list[int] = Field(default_factory=list)
    reference_modes: list[ReferenceModeOption] = Field(default_factory=list)
    ratios_by_resolution: dict[str, list[str]] = Field(default_factory=dict)
    material_limits: GenerateMaterialLimits = Field(default_factory=GenerateMaterialLimits)
