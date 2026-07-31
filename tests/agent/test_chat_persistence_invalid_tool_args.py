"""Seam C: heal 后的非法 tool-arg 路径不得把协议英文落成助手正文"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.subscribers_persistence import ChatPersistenceSubscriber
from app.agent.runtime.turn_engine.events import TurnCompleted, TurnFailed
from app.contracts.metadata import TurnContextMetadata
from app.server.chat.domain.stream_enums import TerminationReason


def _session(*, persist_turn_error: AsyncMock) -> ChatTurnSession:
    """构造 persistence subscriber 所需的最小 session 替身"""
    return cast(
        ChatTurnSession,
        SimpleNamespace(
            terminated_by=None,
            persistence=SimpleNamespace(
                persist_turn_error=persist_turn_error,
                message_ids=[],
            ),
            usage_collector=SimpleNamespace(
                note_failure=lambda **_k: None,
                note_termination=lambda **_k: None,
                tool_recovery_count=0,
            ),
            turn_id="t1",
            browser_blocked=False,
            recovery_hook=SimpleNamespace(
                recovery_cancelled=False,
                recovery_exhausted=False,
                recovery_used=True,
                force_interrupted=False,
            ),
            recorder=SimpleNamespace(final_persisted=True, tool_steps=[], last_model_message=None),
            turn_context_meta=TurnContextMetadata(has_attachments=False, enable_tools=True),
            tool_audit=[],
            ctx=SimpleNamespace(),
            user_id=1,
            conversation_id=1,
            content="hi",
            conversation=SimpleNamespace(id=1, title="会话"),
            agent=None,
            runnable_config=None,
            gate_interrupted=False,
            observation=SimpleNamespace(),
            model_key="m",
            workspace=None,
            turn_asset_ids=(),
        ),
    )


@pytest.mark.asyncio
async def test_completed_after_invalid_tool_heal_does_not_persist_turn_error() -> None:
    """heal 后正常完成时不再二次 persist_turn_error"""
    persist = AsyncMock()
    session = _session(persist_turn_error=persist)
    sub = ChatPersistenceSubscriber(session)
    with patch(
        "app.agent.chat.turn.subscribers_persistence.is_first_user_message",
        new=AsyncMock(return_value=False),
    ):
        await sub.handle(
            TurnCompleted(
                turn_id="t1",
                messages=[],
                answer_text="已改用更短的方案继续",
                tool_calls_count=1,
            ),
            emit=AsyncMock(),
        )
    persist.assert_not_awaited()
    assert session.terminated_by is TerminationReason.COMPLETED


@pytest.mark.asyncio
async def test_protocol_tool_arg_detail_must_not_be_persisted_as_assistant_bubble() -> None:
    """残留 TurnFailed 不得把 assembler 英文协议 detail 写入聊天正文"""
    persist = AsyncMock()
    session = _session(persist_turn_error=persist)
    sub = ChatPersistenceSubscriber(session)
    await sub.handle(
        TurnFailed(
            turn_id="t1",
            error="streamed tool arguments are not valid JSON",
            error_class="gateway_protocol_error",
            step_index=3,
        ),
        emit=AsyncMock(),
    )
    persist.assert_awaited_once()
    content = persist.await_args.kwargs["content"]
    assert "streamed tool arguments" not in content
    assert content
