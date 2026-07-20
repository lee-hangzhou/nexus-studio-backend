from __future__ import annotations

from app.canvas.errors import (
    GENERATION_FAILED,
    INVALID_NODE_ID,
    INVALID_PATCH,
    REVISION_CONFLICT,
)
from app.chat.turn.guards import TurnGuards

_CANVAS_REPEATABLE_ERRORS = {
    "invalid_arguments",
    "unknown_tool",
    "parse_fatal",
    REVISION_CONFLICT,
    INVALID_NODE_ID,
    INVALID_PATCH,
    GENERATION_FAILED,
}


class CanvasTurnGuards(TurnGuards):
    """继承 chat 通用 guard, 扩展画布工具可重复错误判断"""

    def on_tool_finished(self, tool_name: str, error_class: str | None):
        """每次工具结束后判断是否应停止本轮 Agent"""
        self._tool_calls += 1
        if self._tool_calls >= self.max_tool_calls:
            self._last_stop_reason = "max_tools"
            return "stop_turn"
        if error_class in _CANVAS_REPEATABLE_ERRORS:
            key = tool_name or "unknown"
            count = self._tool_errors_by_name.get(key, 0) + 1
            self._tool_errors_by_name[key] = count
            if count >= self.tool_repeat_guard:
                self._last_stop_reason = "error"
                return "stop_turn"
        return "continue"
