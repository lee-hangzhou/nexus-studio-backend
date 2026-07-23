"""Unit tests for Phase 0 turn harness types / guards / gateway mapping."""

from __future__ import annotations

from app.agent.canvas.turn.guards import CanvasTurnGuards
from app.agent.chat.turn.guards import TurnGuards as ChatTurnGuards
from app.agent.runtime.agent.gateway_fail import (
    stream_error_class_for_app_error,
    terminated_by_for_app_error,
    terminated_by_for_error_class,
)
from app.agent.runtime.turn.enums import GuardAction, TurnTerminatedBy
from app.agent.runtime.turn.guards import TurnGuards
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def test_turn_terminated_by_gateway_projection() -> None:
    timeout = terminated_by_for_app_error(AppError(ErrorCode.SERVICE_UNAVAILABLE, "x"))
    assert timeout is TurnTerminatedBy.GATEWAY_UPSTREAM_TIMEOUT
    assert stream_error_class_for_app_error(AppError(ErrorCode.SERVICE_UNAVAILABLE, "x")) == (
        TurnTerminatedBy.GATEWAY_UPSTREAM_TIMEOUT.value
    )
    empty = terminated_by_for_app_error(AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "x"))
    assert empty is TurnTerminatedBy.GATEWAY_EMPTY_STREAM
    upstream = terminated_by_for_app_error(AppError(ErrorCode.GATEWAY_UPSTREAM_ERROR, "x"))
    assert upstream is TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED


def test_terminated_by_for_error_class() -> None:
    assert terminated_by_for_error_class("wall_clock") is TurnTerminatedBy.WALL_CLOCK
    assert terminated_by_for_error_class("unknown_xyz") is TurnTerminatedBy.TURN_FAILED


def test_runtime_guards_max_tools() -> None:
    guards = TurnGuards(
        max_model_steps=10,
        max_tool_calls=2,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    assert guards.on_tool_finished("t", None) is GuardAction.CONTINUE
    assert guards.on_tool_finished("t", None) is GuardAction.STOP_TURN
    assert guards.last_stop_reason == "max_tools"


def test_chat_guards_browser_blocked() -> None:
    guards = ChatTurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=3,
    )
    assert guards.on_tool_finished("browser_navigate", "browser_blocked") is GuardAction.BROWSER_BLOCKED


def test_canvas_guards_do_not_import_chat_turn_guards() -> None:
    import app.agent.canvas.turn.guards as canvas_guards

    assert canvas_guards.TurnGuards.__module__ == "app.agent.runtime.turn.guards"
    guards = CanvasTurnGuards(
        max_model_steps=10,
        max_tool_calls=50,
        wall_clock_sec=60,
        tool_repeat_guard=2,
    )
    assert guards.on_tool_finished("canvas_write", "revision_conflict") is GuardAction.CONTINUE
    assert guards.on_tool_finished("canvas_write", "revision_conflict") is GuardAction.STOP_TURN
