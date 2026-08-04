"""创作器提示词结构化契约（生成页写回 / apply 工具共用）。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class ComposerPromptContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComposerPromptTextSegment(ComposerPromptContract):
    type: Literal["text"]
    text: str


class ComposerPromptMediaSegment(ComposerPromptContract):
    type: Literal["image_url", "video_url", "audio_url"]
    url: str = ""
    asset_id: int | None = Field(default=None, ge=1)


class ComposerPromptTextRefSegment(ComposerPromptContract):
    type: Literal["text_ref"]
    text: str


ComposerPromptContentSegment = Annotated[
    ComposerPromptTextSegment | ComposerPromptMediaSegment | ComposerPromptTextRefSegment,
    Field(discriminator="type"),
]

COMPOSER_PROMPT_CONTENT_ADAPTER: TypeAdapter[list[ComposerPromptContentSegment]] = TypeAdapter(
    list[ComposerPromptContentSegment]
)


class ComposerPromptPayload(ComposerPromptContract):
    """apply_composer_prompt 成功载荷 / SSE composer_prompt_applied 主体。"""

    prompt: str
    content: list[ComposerPromptContentSegment] = Field(default_factory=list)
    ref_asset_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_prompt_or_content(self) -> ComposerPromptPayload:
        if not self.prompt.strip() and not self.content:
            raise ValueError("prompt or content is required")
        return self


class GenerateComposerContext(ComposerPromptContract):
    """流式回合注入的当前创作器草稿快照。"""

    kind: Literal["image", "video", "audio"]
    prompt: str = ""
    content: list[ComposerPromptContentSegment] = Field(default_factory=list)
    model_id: str = ""
    ratio: str | None = None
    resolution: str | None = None
    count: int | None = Field(default=None, ge=1)
    duration: int | None = Field(default=None, ge=1)
    reference_mode: int | None = None
    ref_asset_ids: list[int] = Field(default_factory=list)
