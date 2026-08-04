"""apply_composer_prompt：把结构化提示词写回创作页 Composer。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.contracts.composer_prompt import (
    ComposerPromptContentSegment,
    ComposerPromptPayload,
)


class ApplyComposerPromptInput(BaseModel):
    """apply_composer_prompt 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        description=(
            "写入创作器的纯文本提示词；含 @图片N / @视频N / @音频N 占位时须与 content 中媒体段顺序一致。"
        ),
    )
    content: list[ComposerPromptContentSegment] = Field(
        default_factory=list,
        description=(
            "结构化富文本段，与创作页 TipTap 序列化形状一致："
            "text | image_url | video_url | audio_url | text_ref。"
            "媒体段优先带 asset_id，url 可为空字符串由前端解析。"
        ),
    )
    ref_asset_ids: list[int] = Field(
        default_factory=list,
        description="本提示词引用的资产 id 列表（顺序稳定）；无引用则为空列表。",
    )

    @model_validator(mode="after")
    def _require_prompt_or_content(self) -> ApplyComposerPromptInput:
        if not self.prompt.strip() and not self.content:
            raise ValueError("prompt or content is required")
        for asset_id in self.ref_asset_ids:
            if asset_id < 1:
                raise ValueError("ref_asset_ids must be positive integers")
        return self


def build_apply_composer_prompt_tool() -> StructuredTool:
    """构建 apply_composer_prompt：校验后发出结构化写回载荷。"""

    async def _run(
        prompt: str,
        content: list[ComposerPromptContentSegment] | None = None,
        ref_asset_ids: list[int] | None = None,
    ) -> str:
        try:
            args = ApplyComposerPromptInput.model_validate(
                {
                    "prompt": prompt,
                    "content": content or [],
                    "ref_asset_ids": ref_asset_ids or [],
                }
            )
            payload = ComposerPromptPayload.model_validate(args.model_dump(mode="json"))
        except Exception as exc:
            return str(
                ToolResult.fail(
                    INVALID_ARGUMENTS,
                    detail=str(exc) or "invalid apply_composer_prompt arguments",
                ).to_tool_message()
            )
        return str(
            ToolResult.ok(payload.model_dump_json()).to_tool_message()
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name="apply_composer_prompt",
        description=(
            "将定稿提示词应用到用户创作页 Composer（结构化 content，不是纯文本粘贴）。"
            "在用户确认方向、且你已给出可提交的提示词时调用。"
            "不要用此工具改模型参数、比例、张数或提交生成任务。"
            "Args: prompt（必填除非 content 非空）、content（TipTap 同源分段）、"
            "ref_asset_ids（引用资产 id）。"
            "成功后前端会写回创作框；失败返回 error_type=invalid_arguments。"
        ),
        args_schema=ApplyComposerPromptInput,
    )
