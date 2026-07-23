from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.chat.turn.recovery_hook import ChatRecoveryHook
from app.agent.runtime.agent.events import TurnCompletedEvent
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.run_state import RunState
from app.contracts.metadata import ToolStepMetadata, TurnContextMetadata


@dataclass
class _FakeRecorder:
    tool_steps: list[ToolStepMetadata] = field(default_factory=list)
    final_persisted: bool = False
    last_model_message: object | None = None


def _hook(*, tool_steps: list[ToolStepMetadata]) -> ChatRecoveryHook:
    return ChatRecoveryHook(
        llm=None,
        system_prompt="",
        config={},
        guards=SimpleNamespace(
            max_model_steps=10,
            model_steps_used=0,
            check_wall_clock=lambda: True,
            remaining_wall_clock_seconds=lambda: 60,
        ),
        cancel_event=asyncio.Event(),
        model_key="m",
        turn_id="t1",
        persistence=SimpleNamespace(),
        recorder=_FakeRecorder(tool_steps=tool_steps),
        usage_collector=SimpleNamespace(),
        ctx=SimpleNamespace(),
        turn_context_meta=TurnContextMetadata(has_attachments=False, enable_tools=True),
        tool_audit=[],
        user_id=1,
        conversation_id=1,
        agent=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_before_complete_returns_interrupted_when_user_gate_pending() -> None:
    hook = _hook(
        tool_steps=[
            ToolStepMetadata(
                call_id="c1",
                name="request_user_gate",
                ok=True,
                result_preview="等待用户操作",
            )
        ]
    )
    event = TurnCompletedEvent(turn_id="t1", step_index=2, messages=[])
    state = RunState()
    with patch(
        "app.agent.chat.turn.recovery_hook.has_pending_user_gate",
        new=AsyncMock(return_value=True),
    ):
        assert await hook.before_complete(event, state=state) is TurnTerminatedBy.INTERRUPTED


@pytest.mark.asyncio
async def test_before_complete_interrupted_before_tool_end_has_empty_tool_steps() -> None:
    """Gate interrupt 在 tool.end 前发生时 recorder.tool_steps 仍为空，不得进入 empty_recovery。"""
    hook = _hook(tool_steps=[])
    event = TurnCompletedEvent(turn_id="t1", step_index=0, messages=[])
    state = RunState()
    with patch(
        "app.agent.chat.turn.recovery_hook.has_pending_user_gate",
        new=AsyncMock(return_value=True),
    ):
        assert await hook.before_complete(event, state=state) is TurnTerminatedBy.INTERRUPTED


@pytest.mark.asyncio
async def test_before_complete_still_fails_empty_after_tools_without_gate() -> None:
    hook = _hook(
        tool_steps=[
            ToolStepMetadata(
                call_id="c1",
                name="web_search",
                ok=True,
                result_preview="已完成",
            )
        ]
    )
    event = TurnCompletedEvent(turn_id="t1", step_index=1, messages=[])
    state = RunState()
    with patch(
        "app.agent.chat.turn.recovery_hook.has_pending_user_gate",
        new=AsyncMock(return_value=False),
    ):
        assert (
            await hook.before_complete(event, state=state)
            is TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED
        )
