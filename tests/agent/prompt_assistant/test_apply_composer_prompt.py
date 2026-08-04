"""apply_composer_prompt 入参与成功载荷契约测试。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agent.prompt_assistant.tools.apply_composer_prompt import (
    ApplyComposerPromptInput,
    build_apply_composer_prompt_tool,
)
from app.agent.chat.tools.result import ToolResult
from app.contracts.composer_prompt import ComposerPromptPayload


def test_apply_input_rejects_empty_prompt_and_content() -> None:
    with pytest.raises(ValidationError):
        ApplyComposerPromptInput.model_validate({"prompt": "  ", "content": []})


def test_apply_input_accepts_structured_content() -> None:
    body = ApplyComposerPromptInput.model_validate(
        {
            "prompt": "一只猫 @图片1",
            "content": [
                {"type": "text", "text": "一只猫 "},
                {"type": "image_url", "asset_id": 7, "url": "https://example.com/a.png"},
            ],
            "ref_asset_ids": [7],
        }
    )
    assert body.prompt == "一只猫 @图片1"
    assert body.content[1].type == "image_url"
    assert body.ref_asset_ids == [7]


@pytest.mark.asyncio
async def test_apply_tool_returns_typed_payload_envelope() -> None:
    tool = build_apply_composer_prompt_tool()
    raw = await tool.ainvoke(
        {
            "prompt": "晨光里的街道",
            "content": [{"type": "text", "text": "晨光里的街道"}],
            "ref_asset_ids": [],
        }
    )
    result = ToolResult.parse_tool_message(raw)
    assert result.success is True
    payload = ComposerPromptPayload.model_validate(json.loads(result.output))
    assert payload.prompt == "晨光里的街道"
    assert payload.content[0].type == "text"
