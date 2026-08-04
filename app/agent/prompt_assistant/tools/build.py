"""创作提示词助手本轮工具组装。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from app.agent.canvas.tools.assets import build_get_asset_tool, build_list_assets_tool
from app.agent.prompt_assistant.tools.apply_composer_prompt import build_apply_composer_prompt_tool
from app.agent.prompt_assistant.tools.list_generate_tasks import build_list_generate_tasks_tool
from app.agent.runtime.tools.inspect_turn_media import build_inspect_turn_media_tool
from app.contracts.turn_content import TurnMediaType


def build_prompt_assistant_tools(
    *,
    user_id: int,
    enable_tools: bool,
    tool_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
) -> list[StructuredTool]:
    """组装创作提示词助手工具。

    inspect_turn_media 始终挂载：助手侧栏无附件上传，不能靠「本轮有图」才挂工具，
    否则模型永远看不到 vision；资产范围按用户自有视觉资产校验（含 list/get）。
    """
    _ = tool_asset_ids
    tools: list[StructuredTool] = [
        build_inspect_turn_media_tool(
            user_id=user_id,
            allowed_asset_ids=None,
            asset_media_types=asset_media_types,
        )
    ]
    if not enable_tools:
        return tools
    tools.extend(
        [
            build_list_generate_tasks_tool(user_id=user_id),
            build_list_assets_tool(user_id),
            build_get_asset_tool(user_id),
            build_apply_composer_prompt_tool(),
        ]
    )
    return tools
