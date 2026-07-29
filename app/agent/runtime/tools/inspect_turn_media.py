from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from langchain_core.tools import StructuredTool

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_assets_port
from app.agent.runtime.tools.vision_inspect import (
    MediaInspectTask,
    VISUAL_MEDIA_TYPES,
    inspect_assets_with_vision,
)
from app.contracts.turn_content import TurnMediaType


class InspectTurnMediaRefInput(BaseModel):
    """单条视觉媒体引用"""

    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(ge=1)


class InspectTurnMediaInput(BaseModel):
    """inspect_turn_media 工具入参"""

    model_config = ConfigDict(extra="forbid")

    refs: list[InspectTurnMediaRefInput] = Field(min_length=1, max_length=20)
    task: MediaInspectTask
    instruction: str | None = None

    @model_validator(mode="after")
    def _validate_custom(self) -> InspectTurnMediaInput:
        """custom 任务必须带 instruction"""
        if self.task == MediaInspectTask.CUSTOM:
            if self.instruction is None or not self.instruction.strip():
                raise ValueError("instruction is required when task is custom")
        return self


def build_inspect_turn_media_tool(
    *,
    user_id: int,
    allowed_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
) -> StructuredTool:
    """构建本 turn 视觉理解工具"""

    async def inspect_turn_media(
        refs: list[InspectTurnMediaRefInput],
        task: MediaInspectTask,
        instruction: str | None = None,
    ) -> str:
        """对 allowlist 内图像/视频调用网关视觉理解"""
        if not allowed_asset_ids:
            return ToolResult.fail(
                "file_not_allowed",
                detail="no visual media references in this turn",
            ).to_tool_message()

        ordered_urls: list[str] = []
        ordered_ids: list[int] = []
        ordered_types: list[TurnMediaType] = []
        for ref in refs:
            if ref.asset_id not in allowed_asset_ids:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} is not in turn references",
                ).to_tool_message()
            if ref.asset_id not in asset_media_types:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} media_type missing from turn index",
                ).to_tool_message()
            media_type = asset_media_types[ref.asset_id]
            if media_type not in VISUAL_MEDIA_TYPES:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} is not visual media",
                ).to_tool_message()
            asset = await get_assets_port().get_asset(user_id=user_id, asset_id=ref.asset_id)
            if asset is None or not asset.preview_url.strip():
                return ToolResult.fail(
                    "file_not_found",
                    detail=f"asset {ref.asset_id} not found",
                ).to_tool_message()
            ordered_urls.append(asset.preview_url)
            ordered_ids.append(ref.asset_id)
            ordered_types.append(media_type)

        return await inspect_assets_with_vision(
            task=task,
            instruction=instruction,
            asset_ids=ordered_ids,
            media_types=ordered_types,
            preview_urls=ordered_urls,
            result_extra={"refs": [{"asset_id": aid} for aid in ordered_ids]},
        )

    return StructuredTool.from_function(
        coroutine=inspect_turn_media,
        name="inspect_turn_media",
        description=(
            "Analyze image or video assets attached as this turn's references using the vision model. "
            "Pass refs=[{asset_id}, ...] from Turn References. "
            "task=describe|reverse_prompt|custom; custom requires instruction."
        ),
        args_schema=InspectTurnMediaInput,
    )
