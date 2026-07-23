"""Shared turn budget / fuse guards (mechanism only; surface policies inject eligibility)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from app.agent.runtime.turn.enums import GuardAction

RepeatEligibleFn = Callable[[str, str | None], bool]
StopReason = Literal["max_tools", "error"]


def _never_repeat(_tool_name: str, _error_class: str | None) -> bool:
    return False


class TurnGuards:
    def __init__(
        self,
        *,
        max_model_steps: int,
        max_tool_calls: int,
        wall_clock_sec: int,
        tool_repeat_guard: int,
        is_repeat_eligible: RepeatEligibleFn | None = None,
    ) -> None:
        self.max_model_steps = max_model_steps
        self.max_tool_calls = max_tool_calls
        self.wall_clock_sec = wall_clock_sec
        self.tool_repeat_guard = tool_repeat_guard
        self._is_repeat_eligible = is_repeat_eligible or _never_repeat
        self._started = time.monotonic()
        self._model_steps = 0
        self._tool_calls = 0
        self._tool_errors_by_name: dict[str, int] = {}
        self._last_stop_reason: StopReason | None = None

    @property
    def model_steps_used(self) -> int:
        return self._model_steps

    @property
    def tool_calls_used(self) -> int:
        return self._tool_calls

    def wall_clock_seconds(self) -> float:
        return time.monotonic() - self._started

    def remaining_wall_clock_seconds(self) -> float:
        return max(0.0, self.wall_clock_sec - self.wall_clock_seconds())

    @property
    def last_stop_reason(self) -> StopReason | None:
        return self._last_stop_reason

    def on_model_step_finished(self) -> None:
        self._model_steps += 1

    def exceeded_model_steps(self) -> bool:
        return self._model_steps >= self.max_model_steps

    def check_wall_clock(self) -> bool:
        return (time.monotonic() - self._started) < self.wall_clock_sec

    def check_model_steps(self) -> bool:
        return self._model_steps < self.max_model_steps

    def note_tool_call(self) -> None:
        """Increment tool call budget (call once per finished tool)."""
        self._tool_calls += 1

    def stop_for_max_tools(self) -> bool:
        if self._tool_calls >= self.max_tool_calls:
            self._last_stop_reason = "max_tools"
            return True
        return False

    def apply_repeat_fuse(self, tool_name: str, error_class: str | None) -> GuardAction:
        if self._is_repeat_eligible(tool_name, error_class):
            count = self._tool_errors_by_name.get(tool_name, 0) + 1
            self._tool_errors_by_name[tool_name] = count
            if count >= self.tool_repeat_guard:
                self._last_stop_reason = "error"
                return GuardAction.STOP_TURN
        elif error_class is None:
            self._tool_errors_by_name.pop(tool_name, None)
        return GuardAction.CONTINUE

    def on_tool_finished(self, tool_name: str, error_class: str | None) -> GuardAction:
        self.note_tool_call()
        if self.stop_for_max_tools():
            return GuardAction.STOP_TURN
        return self.apply_repeat_fuse(tool_name, error_class)
