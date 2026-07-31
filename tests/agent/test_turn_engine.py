"""TurnEngine unit tests: guards, termination projection, SSE normalize."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent.runtime.agent.events import ToolFinishedEvent, TurnCompletedEvent
from app.agent.runtime.agent.gateway_fail import terminated_by_for_error_class
from app.agent.runtime.tools.result import BROWSER_BLOCKED, ToolResult
from app.agent.runtime.turn.enums import GuardAction, TurnTerminatedBy
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn_engine.constants import TERMINATION_ERROR_MESSAGES
from app.agent.runtime.turn_engine.handlers import HandlerResult, TurnEventHandlers
from app.agent.runtime.turn_engine.interrupts import parse_interrupt_tool_pending
from app.agent.runtime.turn_engine.run_state import RunState
from app.server.chat.domain.stream_enums import StreamErrorCode, normalize_stream_error_code


def test_normalize_stream_error_code_drops_app_error_compat() -> None:
    assert normalize_stream_error_code("app_error_503") is StreamErrorCode.INTERNAL
    assert (
        normalize_stream_error_code("gateway_upstream_timeout")
        is StreamErrorCode.GATEWAY_UPSTREAM_FAILED
    )
    assert normalize_stream_error_code("wall_clock") is StreamErrorCode.WALL_CLOCK


def test_gateway_fail_maps_to_terminated_by() -> None:
    assert terminated_by_for_error_class("gateway_empty_stream") is (
        TurnTerminatedBy.GATEWAY_EMPTY_STREAM
    )
    assert terminated_by_for_error_class("nope") is TurnTerminatedBy.TURN_FAILED


def test_termination_messages_cover_budget_failures() -> None:
    for key in (
        TurnTerminatedBy.WALL_CLOCK,
        TurnTerminatedBy.MAX_TOOLS,
        TurnTerminatedBy.MAX_ITERATIONS,
        TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED,
    ):
        assert key in TERMINATION_ERROR_MESSAGES
        assert TERMINATION_ERROR_MESSAGES[key]


def test_parse_interrupt_skips_user_gate_payloads() -> None:
    frames = parse_interrupt_tool_pending(
        [
            {
                "gate_type": "credentials",
                "gate_id": "g1",
                "prompt": "login",
                "fields": [],
                "assets": {},
            },
            {
                "action_requests": [
                    {"id": "c1", "name": "apply_canvas_patch", "description": "patch"},
                ]
            },
        ]
    )
    assert frames == [
        {"call_id": "c1", "name": "apply_canvas_patch", "summary": "patch"},
    ]


def test_parse_interrupt_rejects_empty_call_id() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_interrupt_tool_pending([{"call_id": "", "name": "", "summary": "x"}])


@pytest.mark.asyncio
async def test_handlers_stop_on_max_tools() -> None:
    guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=1,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    config = MagicMock()
    config.guards = guards
    config.emit_invalid_tool_call_frames = True
    state = RunState()
    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=state,
        broadcast=lambda _e: None,
        barrier=AsyncMock(),
        fail=AsyncMock(),
    )
    event = ToolFinishedEvent(
        turn_id="t1",
        step_index=0,
        call_id="c1",
        tool_name="tool",
        tool_args={},
        tool_result=ToolResult.ok("ok").to_tool_message(),
        tool_error=False,
        error_class=None,
    )
    result = handlers._handle_tool_finished(event)
    assert result == HandlerResult(action="break", terminated_by=TurnTerminatedBy.MAX_TOOLS)
    assert guards.last_stop_reason == "max_tools"


@pytest.mark.asyncio
async def test_handlers_browser_blocked_completes() -> None:
    from app.agent.chat.turn.guards import TurnGuards as ChatTurnGuards

    guards = ChatTurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    config = MagicMock()
    config.guards = guards
    state = RunState()
    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=state,
        broadcast=lambda _e: None,
        barrier=AsyncMock(),
        fail=AsyncMock(),
    )
    event = ToolFinishedEvent(
        turn_id="t1",
        step_index=0,
        call_id="c1",
        tool_name="browser_navigate",
        tool_args={},
        tool_result=ToolResult.fail(BROWSER_BLOCKED, detail="blocked").to_tool_message(),
        tool_error=True,
        error_class="browser_blocked",
    )
    result = handlers._handle_tool_finished(event)
    assert result.action == "break"
    assert result.terminated_by is TurnTerminatedBy.COMPLETED
    assert guards.on_tool_finished("x", "browser_blocked") is GuardAction.BROWSER_BLOCKED


@pytest.mark.asyncio
async def test_handlers_cancel_from_recovery_skips_fail() -> None:
    config = MagicMock()
    config.guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    state = RunState()
    fail = AsyncMock()

    class _Hook:
        async def after_model_step(self, event, *, state):
            return None

        async def before_complete(self, event, *, state):
            return TurnTerminatedBy.CANCELLED

    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=state,
        broadcast=lambda _e: None,
        barrier=AsyncMock(),
        fail=fail,
        recovery_hook=_Hook(),
    )
    result = await handlers._handle_turn_completed(
        TurnCompletedEvent(turn_id="t1", step_index=0, messages=[AIMessage(content="")])
    )
    assert result.terminated_by is TurnTerminatedBy.CANCELLED
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_handlers_before_complete_interrupted_broadcasts_completed_not_fail() -> None:
    config = MagicMock()
    config.guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    state = RunState()
    fail = AsyncMock()
    barrier = AsyncMock()
    broadcasted: list = []

    class _Hook:
        async def after_model_step(self, event, *, state):
            return None

        async def before_complete(self, event, *, state):
            return TurnTerminatedBy.INTERRUPTED

    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=state,
        broadcast=broadcasted.append,
        barrier=barrier,
        fail=fail,
        recovery_hook=_Hook(),
    )
    result = await handlers._handle_turn_completed(
        TurnCompletedEvent(turn_id="t1", step_index=0, messages=[AIMessage(content="")])
    )
    assert result.terminated_by is TurnTerminatedBy.INTERRUPTED
    assert state.terminated_by is TurnTerminatedBy.INTERRUPTED
    fail.assert_not_awaited()
    barrier.assert_awaited()
    assert len(broadcasted) == 1


@pytest.mark.asyncio
async def test_handlers_force_interrupted_skips_completed() -> None:
    config = MagicMock()
    config.guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    state = RunState()
    state.answer_parts.append("hi")

    class _Hook:
        force_interrupted = True

        async def after_model_step(self, event, *, state):
            return None

        async def before_complete(self, event, *, state):
            return None

    barrier = AsyncMock()
    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=state,
        broadcast=lambda _e: None,
        barrier=barrier,
        fail=AsyncMock(),
        recovery_hook=_Hook(),
    )
    result = await handlers._handle_turn_completed(
        TurnCompletedEvent(turn_id="t1", step_index=0, messages=[AIMessage(content="hi")])
    )
    assert result.terminated_by is TurnTerminatedBy.INTERRUPTED
    assert state.terminated_by is TurnTerminatedBy.INTERRUPTED
    barrier.assert_awaited()


@pytest.mark.asyncio
async def test_sse_subscriber_owns_done_after_failure() -> None:
    from app.agent.runtime.stream.frames import StreamFrameType
    from app.agent.runtime.turn_engine.events import TurnEnded, TurnFailed
    from app.agent.runtime.turn_engine.sse_subscriber import SseTurnSubscriber
    from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy

    frames: list = []

    async def emit(frame) -> None:
        frames.append(frame)

    sub = SseTurnSubscriber(
        policy=SseTerminalPolicy(emit_done_on_completed=True, emit_done_after_failure=True)
    )
    await sub.handle(
        TurnFailed(turn_id="t1", error="x", error_class="empty_response", step_index=0),
        emit=emit,
    )
    await sub.handle(
        TurnEnded(turn_id="t1", terminated_by=TurnTerminatedBy.EMPTY_RESPONSE, failed_emitted=True),
        emit=emit,
    )
    types = [f.type for f in frames]
    assert types == [StreamFrameType.ERROR, StreamFrameType.DONE]


@pytest.mark.asyncio
async def test_canvas_empty_hook_rejects_blank_answer() -> None:
    from app.agent.canvas.turn.empty_hook import CanvasEmptyAnswerHook
    from app.agent.runtime.agent.events import TurnCompletedEvent

    hook = CanvasEmptyAnswerHook()
    state = RunState()
    result = await hook.before_complete(
        TurnCompletedEvent(turn_id="t1", step_index=0, messages=[]),
        state=state,
    )
    assert result is TurnTerminatedBy.EMPTY_RESPONSE
    state.answer_parts.append("ok")
    assert (
        await hook.before_complete(
            TurnCompletedEvent(turn_id="t1", step_index=0, messages=[]),
            state=state,
        )
        is None
    )


def test_termination_reason_maps_engine_vocab() -> None:
    from app.agent.runtime.turn.termination import termination_reason_for
    from app.server.chat.domain.stream_enums import TerminationReason

    assert termination_reason_for(TurnTerminatedBy.COMPLETED) is TerminationReason.COMPLETED
    assert termination_reason_for(TurnTerminatedBy.MAX_ITERATIONS) is TerminationReason.MAX_STEPS
    assert termination_reason_for(TurnTerminatedBy.MAX_TOOLS) is TerminationReason.MAX_TOOLS
    assert termination_reason_for(TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED) is (
        TerminationReason.ERROR
    )


def test_runtime_has_no_chat_surface_imports() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "agent" / "runtime"
    # 历史债：inspect_turn_media 仍依赖 chat 表面；其余 runtime 禁止新增
    allow_files = {"inspect_turn_media.py"}
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name in allow_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
                "app.agent.chat"
            ):
                offenders.append(f"{path}:{node.lineno}:{node.module}")
    assert offenders == []


def test_interrupt_model_separates_gate_and_tool_approval() -> None:
    from app.agent.runtime.turn_engine.interrupt_model import (
        PendingToolAction,
        UserGateInterrupt,
        classify_interrupt_value,
        parse_pending_tool_actions,
    )

    gate = classify_interrupt_value(
        {"gate_type": "credentials", "gate_id": "g1", "prompt": "login"}
    )
    assert len(gate) == 1 and isinstance(gate[0], UserGateInterrupt)

    with pytest.raises(ValidationError, match="action_request.id"):
        parse_pending_tool_actions(
            [
                {
                    "action_requests": [
                        {"id": "c1", "name": "apply_canvas_patch", "description": "patch"},
                        {"id": "", "name": "bad", "description": "x"},
                    ]
                }
            ]
        )

    actions = parse_pending_tool_actions(
        [
            {
                "action_requests": [
                    {"id": "c1", "name": "apply_canvas_patch", "description": "patch"},
                ]
            }
        ]
    )
    assert len(actions) == 1
    assert isinstance(actions[0], PendingToolAction)
    assert actions[0].call_id == "c1"


@pytest.mark.asyncio
async def test_handlers_heal_invalid_tool_calls_by_default() -> None:
    from app.agent.runtime.agent.events import InvalidToolCall, ModelStepFinishedEvent

    guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    config = MagicMock()
    config.guards = guards
    config.emit_invalid_tool_call_frames = True
    config.heal_invalid_tool_calls = True
    fail = AsyncMock()
    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=RunState(),
        broadcast=lambda _e: None,
        barrier=AsyncMock(),
        fail=fail,
    )
    result = await handlers._handle_model_step_finished(
        ModelStepFinishedEvent(
            turn_id="t1",
            step_index=0,
            ai_message=AIMessage(content="", tool_calls=[]),
            invalid_tool_calls=[
                InvalidToolCall(
                    call_id="c1",
                    name="tool",
                    raw_arguments="{",
                    parse_error="bad json",
                )
            ],
        )
    )
    assert result.action == "continue"
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_handlers_heal_streamed_invalid_tool_argument_json_without_failing_turn() -> None:
    """流式非法 tool JSON 走 heal 合成 tool 错误帧, 不 TurnFailed."""
    from app.agent.runtime.agent.events import InvalidToolCall, ModelStepFinishedEvent
    from app.agent.runtime.turn_engine.events import ToolFinished, ToolStarted

    guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    config = MagicMock()
    config.guards = guards
    config.emit_invalid_tool_call_frames = True
    config.heal_invalid_tool_calls = True
    fail = AsyncMock()
    emitted: list[object] = []
    handlers = TurnEventHandlers(
        turn_id="t1",
        config=config,
        state=RunState(),
        broadcast=emitted.append,
        barrier=AsyncMock(),
        fail=fail,
    )
    result = await handlers._handle_model_step_finished(
        ModelStepFinishedEvent(
            turn_id="t1",
            step_index=3,
            ai_message=AIMessage(content="", tool_calls=[]),
            invalid_tool_calls=[
                InvalidToolCall(
                    call_id="call_1",
                    name="execute_python",
                    raw_arguments='{"code": "unterm',
                    parse_error="streamed tool arguments are not valid JSON: Unterminated string",
                )
            ],
        )
    )
    assert result.action == "continue"
    fail.assert_not_awaited()
    assert any(isinstance(e, ToolStarted) and e.synthetic for e in emitted)
    assert any(
        isinstance(e, ToolFinished) and e.synthetic and e.tool_error for e in emitted
    )
