from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.chat.memory.message_validate import (
    has_unresolved_tool_calls,
    repair_unresolved_tool_calls,
)
from app.agent.chat.turn.checkpoint import repair_chat_checkpoint_if_needed


@pytest.mark.asyncio
async def test_repair_chat_checkpoint_skips_when_user_gate_pending() -> None:
    agent = MagicMock()
    config = {}
    with patch(
        "app.agent.chat.turn.checkpoint.has_pending_user_gate",
        new=AsyncMock(return_value=True),
    ) as gate, patch(
        "app.agent.chat.turn.checkpoint.repair_canvas_checkpoint_if_needed",
        new=AsyncMock(return_value=True),
    ) as canvas_repair:
        assert (
            await repair_chat_checkpoint_if_needed(
                agent,
                config,
                conversation_id=1,
                turn_id="t1",
            )
            is False
        )
        gate.assert_awaited_once()
        canvas_repair.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_chat_checkpoint_writes_when_no_gate() -> None:
    agent = MagicMock()
    config = {}
    with patch(
        "app.agent.chat.turn.checkpoint.has_pending_user_gate",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.agent.chat.turn.checkpoint.repair_canvas_checkpoint_if_needed",
        new=AsyncMock(return_value=True),
    ) as canvas_repair:
        assert (
            await repair_chat_checkpoint_if_needed(
                agent,
                config,
                conversation_id=3,
                turn_id="t2",
                reason="stale_unresolved",
            )
            is True
        )
        canvas_repair.assert_awaited_once()
        kwargs = canvas_repair.await_args.kwargs
        assert kwargs["project_id"] == 3
        assert kwargs["reason"] == "stale_unresolved"


def test_repair_unresolved_logs_source() -> None:
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "x"}}],
        ),
    ]
    assert has_unresolved_tool_calls(messages)
    repaired = repair_unresolved_tool_calls(messages, source="outbound_last_resort")
    assert not has_unresolved_tool_calls(repaired)
