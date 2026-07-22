import time
from typing import Literal

from app.agent.chat.turn.tool_repeat_policy import is_repeat_guard_eligible


class TurnGuards:
    def __init__(
        self,
        *,
        max_model_steps: int,
        max_tool_calls: int,
        wall_clock_sec: int,
        tool_repeat_guard: int,
    ) -> None:
        self.max_model_steps = max_model_steps
        self.max_tool_calls = max_tool_calls
        self.wall_clock_sec = wall_clock_sec
        self.tool_repeat_guard = tool_repeat_guard
        self._started = time.monotonic()
        self._model_steps = 0
        self._tool_calls = 0
        self._tool_errors_by_name: dict[str, int] = {}
        self._last_stop_reason: Literal["max_tools", "error"] | None = None

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
    def last_stop_reason(self) -> Literal["max_tools", "error"] | None:
        return self._last_stop_reason

    def on_model_step_finished(self) -> None:
        self._model_steps += 1

    def exceeded_model_steps(self) -> bool:
        return self._model_steps >= self.max_model_steps

    def check_wall_clock(self) -> bool:
        return (time.monotonic() - self._started) < self.wall_clock_sec

    def check_model_steps(self) -> bool:
        return self._model_steps < self.max_model_steps

    def on_tool_finished(
        self, tool_name: str, error_class: str | None
    ) -> Literal["continue", "stop_turn", "browser_blocked"]:
        self._tool_calls += 1
        if error_class == "browser_blocked":
            return "browser_blocked"
        if self._tool_calls >= self.max_tool_calls:
            self._last_stop_reason = "max_tools"
            return "stop_turn"
        if error_class == "gate_setup_failed":
            count = self._tool_errors_by_name.get(tool_name, 0) + 1
            self._tool_errors_by_name[tool_name] = count
            if count >= 1:
                self._last_stop_reason = "error"
                return "stop_turn"
        if is_repeat_guard_eligible(tool_name=tool_name, error_class=error_class):
            count = self._tool_errors_by_name.get(tool_name, 0) + 1
            self._tool_errors_by_name[tool_name] = count
            if count >= self.tool_repeat_guard:
                self._last_stop_reason = "error"
                return "stop_turn"
        elif error_class is None:
            self._tool_errors_by_name.pop(tool_name, None)
        return "continue"
