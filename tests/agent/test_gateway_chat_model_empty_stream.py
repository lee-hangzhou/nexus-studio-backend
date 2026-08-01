"""Completed empty model streams are valid steps, not gateway_empty_stream errors."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import ModelSpec
from app.agent.chat.llm.thinking import thinking_config_from_registry


def _llm() -> GatewayChatModel:
    return GatewayChatModel(
        model_key="test-model",
        spec=ModelSpec(
            family="openai",
            gateway_model="test-model",
            display_name="test",
            context_budget=128000,
            thinking=thinking_config_from_registry(None),
            supports_vision=False,
        ),
    )


async def _empty_completed_stream(*_args, **_kwargs):
    """Mirrors upstream 200 with role + empty deltas + stop (no content/tool_calls)."""
    frames = (
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    )
    for line in frames:
        yield line


@pytest.mark.asyncio
async def test_completed_empty_stream_yields_assembled_step_without_raising() -> None:
    llm = _llm()
    with patch(
        "app.agent.chat.llm.gateway_chat_model.gateway_client.openai_chat_stream",
        new=_empty_completed_stream,
    ):
        chunks = [
            chunk
            async for chunk in llm._astream([HumanMessage(content="hi")])
        ]

    assert chunks
    final = chunks[-1]
    assert final.generation_info["assembled_step"] is True
    assert final.generation_info["assembled_content"] == ""
    assert final.generation_info["finish_reason"] == "stop"
    assert list(final.message.tool_calls or []) == []
