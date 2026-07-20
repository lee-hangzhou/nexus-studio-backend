from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.generation.enums import GenerationKind, ReferenceMode

NodeExecuteKind = Literal["text", "image", "video", "audio"]


class SubmitManualRefInput(BaseModel):
    """手动提交时的上传引用（与前端 manualRefs 对齐）。"""

    model_config = ConfigDict(extra="forbid")

    asset_id: int | None = None
    material_id: int | None = None


class SubmitNodeExecuteInput(BaseModel):
    """画布节点手动执行入参（非 Agent turn）。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: NodeExecuteKind
    prompt: str = Field(min_length=1)
    model_key: str | None = None
    model_id: str | None = None
    voice_id: str | None = None
    ratio: str | None = None
    resolution: str | None = None
    count: int = Field(default=1, ge=1, le=6)
    duration: int | None = Field(default=None, ge=3, le=15)
    reference_mode: ReferenceMode | None = None
    ref_attachment_ids: list[int] = Field(default_factory=list)
    ref_asset_ids: list[int] = Field(default_factory=list)
    submit_content: list[dict[str, Any]] | None = Field(
        default=None,
        description="编辑器 WorkflowPromptContent，用于 refs 校验",
    )
    manual_refs: list[SubmitManualRefInput] = Field(default_factory=list)
    preview_media_asset_ids: list[int] = Field(
        default_factory=list,
        description="连线 media mention 的 assetId 列表（顺序与 UI 一致）",
    )
    expected_revision: int | None = None

    def to_generation_input(self) -> "SubmitNodeGenerationInput":
        """image/video/audio 异步生成参数。"""
        from app.canvas.tools.generation import SubmitNodeGenerationInput

        if self.kind not in ("image", "video", "audio"):
            raise ValueError("not a media generation kind")
        if not self.model_id:
            raise ValueError("model_id required")
        return SubmitNodeGenerationInput(
            node_id=self.node_id,
            kind=GenerationKind(self.kind),
            prompt=self.prompt,
            model_id=self.model_id,
            voice_id=self.voice_id,
            ratio=self.ratio,
            resolution=self.resolution,
            count=self.count,
            duration=self.duration,
            reference_mode=self.reference_mode,
            ref_attachment_ids=self.ref_attachment_ids,
            ref_asset_ids=self.ref_asset_ids,
            expected_revision=self.expected_revision,
        )
