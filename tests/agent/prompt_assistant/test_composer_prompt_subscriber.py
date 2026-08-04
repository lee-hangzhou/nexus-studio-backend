"""composer_prompt_applied 产品侧订阅者：成功发写回帧，畸形载荷发 ERROR。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.chat.tools.result import ToolResult
from app.agent.prompt_assistant.subscribers import (
    APPLY_COMPOSER_PROMPT_TOOL,
    PromptAssistantComposerPromptSubscriber,
)
from app.agent.runtime.stream.frames import StreamFrameType
from app.agent.runtime.turn_engine.events import ToolFinished
from app.server.chat.domain.stream_enums import StreamErrorCode


def _capture_emit(frames: list[Any]):
    """构造异步 emit，收集帧。"""

    async def _emit(frame: Any) -> None:
        frames.append(frame)

    return _emit


@pytest.mark.asyncio
async def test_composer_prompt_subscriber_emits_applied_frame_on_success() -> None:
    frames: list[Any] = []
    payload = {
        "prompt": "一只猫 @图片1",
        "content": [
            {"type": "text", "text": "一只猫 "},
            {"type": "image_url", "asset_id": 7, "url": ""},
        ],
        "ref_asset_ids": [7],
    }
    event = ToolFinished(
        turn_id="t1",
        step_index=1,
        call_id="c1",
        tool_name=APPLY_COMPOSER_PROMPT_TOOL,
        tool_result=ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message(),
        tool_error=False,
        error_class=None,
    )
    await PromptAssistantComposerPromptSubscriber().handle(event, emit=_capture_emit(frames))
    assert len(frames) == 1
    frame = frames[0]
    assert frame.type == StreamFrameType.COMPOSER_PROMPT_APPLIED
    assert frame.prompt == "一只猫 @图片1"
    assert frame.ref_asset_ids == [7]
    assert frame.content[1].type == "image_url"
    assert frame.content[1].asset_id == 7


@pytest.mark.asyncio
async def test_composer_prompt_subscriber_emits_error_on_invalid_payload() -> None:
    frames: list[Any] = []
    event = ToolFinished(
        turn_id="t2",
        step_index=1,
        call_id="c2",
        tool_name=APPLY_COMPOSER_PROMPT_TOOL,
        tool_result=ToolResult.ok("{}").to_tool_message(),
        tool_error=False,
        error_class=None,
    )
    await PromptAssistantComposerPromptSubscriber().handle(event, emit=_capture_emit(frames))
    assert len(frames) == 1
    frame = frames[0]
    assert frame.type == StreamFrameType.ERROR
    assert frame.code == StreamErrorCode.INTERNAL.value


@pytest.mark.asyncio
async def test_composer_prompt_subscriber_ignores_other_tools() -> None:
    frames: list[Any] = []
    event = ToolFinished(
        turn_id="t3",
        step_index=1,
        call_id="c3",
        tool_name="list_assets",
        tool_result=ToolResult.ok("[]").to_tool_message(),
        tool_error=False,
        error_class=None,
    )
    await PromptAssistantComposerPromptSubscriber().handle(event, emit=_capture_emit(frames))
    assert frames == []
