from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from langchain_core.tools import StructuredTool

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_assets_port, get_canvas_port
from app.agent.runtime.tools.vision_inspect import (
    MediaInspectTask,
    VISUAL_MEDIA_TYPES,
    inspect_assets_with_vision,
    media_type_from_mime,
)
from app.server.canvas.domain.node_data import data_output_asset_ids, parse_node_data


class InspectNodeMediaInput(BaseModel):
    """inspect_node_media 工具入参"""

    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(min_length=1, max_length=20)
    task: MediaInspectTask
    instruction: str | None = None

    @field_validator("node_ids")
    @classmethod
    def _require_uuid_node_ids(cls, value: list[str]) -> list[str]:
        """节点 id 必须为 UUID 字符串"""
        for node_id in value:
            UUID(node_id)
        return value

    @model_validator(mode="after")
    def _validate_custom(self) -> InspectNodeMediaInput:
        """custom 任务必须带 instruction"""
        if self.task == MediaInspectTask.CUSTOM:
            if self.instruction is None or not self.instruction.strip():
                raise ValueError("instruction is required when task is custom")
        return self


def build_inspect_node_media_tool(*, project_id: int, episode_id: int, user_id: int) -> StructuredTool:
    """构建节点产出媒体视觉理解工具"""

    async def inspect_node_media(
        node_ids: list[str],
        task: MediaInspectTask,
        instruction: str | None = None,
    ) -> str:
        """对本项目本集节点 output_asset_ids 调用网关视觉理解"""
        canvas = get_canvas_port()
        assets_port = get_assets_port()
        graph = await canvas.get_graph(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            node_ids=tuple(node_ids),
            include_edges=False,
            include_asset_urls=False,
        )
        by_id = {str(row.id): row for row in graph.nodes}
        ordered_ids: list[int] = []
        ordered_types = []
        ordered_urls: list[str] = []
        seen: set[int] = set()
        resolved_node_ids: list[str] = []

        for node_id in node_ids:
            row = by_id.get(node_id)
            if row is None:
                return ToolResult.fail(
                    "invalid_node_id",
                    detail=f"node {node_id} not found in this project episode",
                ).to_tool_message()
            resolved_node_ids.append(node_id)
            data = parse_node_data(row.data)
            asset_ids = data_output_asset_ids(data)
            if not asset_ids:
                return ToolResult.fail(
                    "file_not_found",
                    detail=f"node {node_id} has no output_asset_ids",
                ).to_tool_message()
            for asset_id in asset_ids:
                if asset_id in seen:
                    continue
                asset = await assets_port.get_asset(user_id=user_id, asset_id=asset_id)
                if asset is None or not asset.preview_url.strip():
                    return ToolResult.fail(
                        "file_not_found",
                        detail=f"asset {asset_id} not found",
                    ).to_tool_message()
                if not asset.mime_type:
                    return ToolResult.fail(
                        "file_not_allowed",
                        detail=f"asset {asset_id} missing mime_type",
                    ).to_tool_message()
                media_type = media_type_from_mime(asset.mime_type)
                if media_type is None or media_type not in VISUAL_MEDIA_TYPES:
                    return ToolResult.fail(
                        "file_not_allowed",
                        detail=f"asset {asset_id} is not visual media",
                    ).to_tool_message()
                seen.add(asset_id)
                ordered_ids.append(asset_id)
                ordered_types.append(media_type)
                ordered_urls.append(asset.preview_url)

        if not ordered_ids:
            return ToolResult.fail(
                "file_not_found",
                detail="no visual output assets on the requested nodes",
            ).to_tool_message()

        return await inspect_assets_with_vision(
            task=task,
            instruction=instruction,
            asset_ids=ordered_ids,
            media_types=ordered_types,
            preview_urls=ordered_urls,
            result_extra={
                "node_ids": resolved_node_ids,
                "asset_ids": ordered_ids,
            },
        )

    return StructuredTool.from_function(
        coroutine=inspect_node_media,
        name="inspect_node_media",
        description=(
            "Analyze image/video outputs on canvas nodes via data.output_asset_ids. "
            "Pass node_ids from this episode (query/patch). "
            "task=describe|reverse_prompt|custom; custom requires instruction. "
            "Not for turn-attachment media — use inspect_turn_media for Turn References."
        ),
        args_schema=InspectNodeMediaInput,
    )
