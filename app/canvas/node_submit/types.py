from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

WorkflowPromptContent = list[dict[str, Any]]

MentionMediaKind = Literal["image", "video", "audio", "text"]


@dataclass(frozen=True)
class ManualMaterialRef:
    asset_id: int | None = None
    material_id: int | None = None


@dataclass(frozen=True)
class MentionItemRef:
    """parity / manual 校验用的精简 mention（仅 asset 顺序）"""

    asset_id: int | None = None
    type: MentionMediaKind = "image"


@dataclass(frozen=True)
class SubmitMaterialRefs:
    ref_asset_ids: tuple[int, ...]
    ref_attachment_ids: tuple[int, ...]


@dataclass(frozen=True)
class PrepareNodeSubmitResult:
    prompt: str
    ref_asset_ids: tuple[int, ...]
    ref_attachment_ids: tuple[int, ...]
