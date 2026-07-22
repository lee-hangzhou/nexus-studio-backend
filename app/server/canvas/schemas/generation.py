from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.server.generation.domain.enums import GenerationKind, ReferenceMode


class SubmitNodeGenerationInput(BaseModel):
    """submit_node_generation 工具入参 schema"""

    node_id: str
    kind: GenerationKind
    prompt: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    voice_id: str | None = None
    ratio: str | None = None
    resolution: str | None = None
    count: int = Field(default=1, ge=1, le=6)
    duration: int | None = Field(default=None, ge=3, le=15)
    reference_mode: ReferenceMode | None = None
    ref_attachment_ids: list[int] = Field(default_factory=list)
    ref_asset_ids: list[int] = Field(default_factory=list)
    expected_revision: int | None = Field(
        default=None,
        description="可选 revision CAS；提交生成并更新节点时用于防止旧状态覆盖",
    )
