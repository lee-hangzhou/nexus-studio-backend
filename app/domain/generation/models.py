from dataclasses import dataclass

from app.domain.generation.enums import (
    GenerationKind,
    MaterialType,
    ReferenceMode,
)


@dataclass(frozen=True)
class MaterialLimits:
    images: int = 0
    videos: int = 0
    audios: int = 0
    requires_any: bool = False
    allow_audio_only: bool = False

    def limit_for(self, material_type: MaterialType) -> int:
        if material_type == MaterialType.IMAGE:
            return self.images
        if material_type == MaterialType.VIDEO:
            return self.videos
        return self.audios


@dataclass(frozen=True)
class GenerationModelCapabilities:
    model_id: str
    kind: GenerationKind
    ratios: tuple[str, ...]
    resolutions: tuple[str, ...]
    counts: tuple[int, ...]
    durations: tuple[int, ...]
    reference_modes: tuple[ReferenceMode, ...]
    material_limits: MaterialLimits
    ratios_by_resolution: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def default_reference_mode(self) -> ReferenceMode | None:
        return self.reference_modes[0] if self.reference_modes else None

    @property
    def default_duration(self) -> int | None:
        return self.durations[0] if self.durations else None

    def ratios_for_resolution(self, resolution: str | None) -> tuple[str, ...]:
        if resolution is not None:
            for configured_resolution, ratios in self.ratios_by_resolution:
                if configured_resolution == resolution:
                    return ratios
        return self.ratios
