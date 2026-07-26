"""Canvas-surface turn guards: canvas-specific repeatable tool errors."""

from __future__ import annotations

from app.agent.runtime.turn.enums import GuardAction
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.canvas.errors import (
    GENERATION_FAILED,
    INVALID_NODE_ID,
    INVALID_PATCH,
    REVISION_CONFLICT,
)

_CANVAS_REPEATABLE_ERRORS = frozenset(
    {
        "invalid_arguments",
        "unknown_tool",
        "parse_fatal",
        REVISION_CONFLICT,
        INVALID_NODE_ID,
        INVALID_PATCH,
        GENERATION_FAILED,
    }
)


class CanvasTurnGuards(TurnGuards):
    """Runtime TurnGuards with canvas tool repeat fuse."""

    def on_tool_finished(self, tool_name: str, error_class: str | None) -> GuardAction:
        self.note_tool_call()
        if self.stop_for_max_tools():
            return GuardAction.STOP_TURN
        if error_class in _CANVAS_REPEATABLE_ERRORS:
            count = self._tool_errors_by_name.get(tool_name, 0) + 1
            self._tool_errors_by_name[tool_name] = count
            if count >= self.tool_repeat_guard:
                self._last_stop_reason = "error"
                return GuardAction.STOP_TURN
        return GuardAction.CONTINUE
