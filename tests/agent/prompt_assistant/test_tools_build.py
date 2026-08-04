"""prompt_assistant 工具组装：vision 必须始终挂载。"""

from __future__ import annotations

from app.agent.prompt_assistant.tools.build import build_prompt_assistant_tools
from app.contracts.turn_content import TurnMediaType


def test_prompt_assistant_always_mounts_inspect_turn_media() -> None:
    tools = build_prompt_assistant_tools(
        user_id=1,
        enable_tools=True,
        tool_asset_ids=frozenset(),
        asset_media_types={},
    )
    names = [tool.name for tool in tools]
    assert "inspect_turn_media" in names
    assert "apply_composer_prompt" in names


def test_prompt_assistant_inspect_still_mounted_when_tools_disabled() -> None:
    tools = build_prompt_assistant_tools(
        user_id=1,
        enable_tools=False,
        tool_asset_ids=frozenset(),
        asset_media_types={7: TurnMediaType.IMAGE},
    )
    assert [tool.name for tool in tools] == ["inspect_turn_media"]
