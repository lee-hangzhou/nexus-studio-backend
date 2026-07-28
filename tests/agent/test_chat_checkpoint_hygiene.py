from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.chat.memory.message_validate import (
    has_unresolved_tool_calls,
    repair_unresolved_tool_calls,
)
from app.agent.chat.turn.checkpoint import repair_chat_checkpoint_if_needed
from app.agent.runtime.turn_engine.checkpoint import repair_unresolved_checkpoint_if_needed


def _agent_with_messages(messages: list) -> MagicMock:
    """构造带 aget_state/aupdate_state 的假 agent"""
    agent = MagicMock()
    snap = MagicMock()
    snap.values = {"messages": messages}
    agent.aget_state = AsyncMock(return_value=snap)
    agent.aupdate_state = AsyncMock()
    return agent


@pytest.mark.asyncio
async def test_repair_chat_checkpoint_skips_when_user_gate_pending() -> None:
    agent = _agent_with_messages([])
    with patch(
        "app.agent.chat.turn.checkpoint.has_pending_user_gate",
        new=AsyncMock(return_value=True),
    ) as gate, patch(
        "app.agent.chat.turn.checkpoint.repair_unresolved_checkpoint_if_needed",
        new=AsyncMock(return_value=2),
    ) as shared_repair:
        assert (
            await repair_chat_checkpoint_if_needed(
                agent,
                {},
                conversation_id=1,
                turn_id="t1",
            )
            is False
        )
        gate.assert_awaited_once()
        shared_repair.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_chat_checkpoint_writes_when_no_gate() -> None:
    agent = MagicMock()
    with patch(
        "app.agent.chat.turn.checkpoint.has_pending_user_gate",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.agent.chat.turn.checkpoint.repair_unresolved_checkpoint_if_needed",
        new=AsyncMock(return_value=3),
    ) as shared_repair:
        assert (
            await repair_chat_checkpoint_if_needed(
                agent,
                {},
                conversation_id=3,
                turn_id="t2",
                reason="stale_unresolved",
            )
            is True
        )
        shared_repair.assert_awaited_once_with(
            agent,
            {},
            reason="stale_unresolved",
        )


@pytest.mark.asyncio
async def test_repair_chat_checkpoint_noop_when_shared_returns_none() -> None:
    agent = MagicMock()
    with patch(
        "app.agent.chat.turn.checkpoint.has_pending_user_gate",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.agent.chat.turn.checkpoint.repair_unresolved_checkpoint_if_needed",
        new=AsyncMock(return_value=None),
    ):
        assert (
            await repair_chat_checkpoint_if_needed(
                agent,
                {},
                conversation_id=3,
                turn_id="t3",
            )
            is False
        )


@pytest.mark.asyncio
async def test_shared_repair_writes_synthetic_tool_messages() -> None:
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "x"}}],
        ),
    ]
    agent = _agent_with_messages(messages)
    assert await repair_unresolved_checkpoint_if_needed(agent, {}, reason="stale_unresolved") == 2
    agent.aupdate_state.assert_awaited_once()
    written = agent.aupdate_state.await_args.args[1]["messages"]
    repaired = written[1:]
    assert not has_unresolved_tool_calls(repaired)
    assert isinstance(repaired[-1], ToolMessage)
    assert repaired[-1].tool_call_id == "c1"


@pytest.mark.asyncio
async def test_shared_repair_noop_when_clean() -> None:
    agent = _agent_with_messages([HumanMessage(content="hi")])
    assert await repair_unresolved_checkpoint_if_needed(agent, {}, reason="stale_unresolved") is None
    agent.aupdate_state.assert_not_awaited()


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
