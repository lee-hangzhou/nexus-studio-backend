"""Chat-surface turn guards: browser / gate fuses on top of runtime TurnGuards."""

from __future__ import annotations

from app.agent.chat.turn.tool_repeat_policy import is_repeat_guard_eligible
from app.agent.runtime.turn.enums import GuardAction
from app.agent.runtime.turn.guards import TurnGuards as RuntimeTurnGuards


class TurnGuards(RuntimeTurnGuards):
    def __init__(
        self,
        *,
        max_model_steps: int,
        max_tool_calls: int,
        wall_clock_sec: int,
        tool_repeat_guard: int,
    ) -> None:
        super().__init__(
            max_model_steps=max_model_steps,
            max_tool_calls=max_tool_calls,
            wall_clock_sec=wall_clock_sec,
            tool_repeat_guard=tool_repeat_guard,
            is_repeat_eligible=is_repeat_guard_eligible,
        )

    def on_tool_finished(self, tool_name: str, error_class: str | None) -> GuardAction:
        self.note_tool_call()
        if error_class == "browser_blocked":
            return GuardAction.BROWSER_BLOCKED
        if self.stop_for_max_tools():
            return GuardAction.STOP_TURN
        if error_class == "gate_setup_failed":
            count = self._tool_errors_by_name.get(tool_name, 0) + 1
            self._tool_errors_by_name[tool_name] = count
            if count >= 1:
                self._last_stop_reason = "error"
                return GuardAction.STOP_TURN
        return self.apply_repeat_fuse(tool_name, error_class)
