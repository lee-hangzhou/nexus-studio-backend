from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

WorkflowPromptContent = list[dict[str, Any]]

MentionMediaKind = Literal["image", "video", "audio"]


@dataclass(frozen=True)
class ManualMaterialRef:
    asset_id: int


@dataclass(frozen=True)
class MentionItemRef:
    """parity / manual 校验用的精简 media mention（仅 asset 顺序）"""

    asset_id: int
    type: MentionMediaKind


@dataclass(frozen=True)
class SubmitMaterialRefs:
    ref_asset_ids: tuple[int, ...]


@dataclass(frozen=True)
class PrepareNodeSubmitResult:
    prompt: str
    ref_asset_ids: tuple[int, ...]
