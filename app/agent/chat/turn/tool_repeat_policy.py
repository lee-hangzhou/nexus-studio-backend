"""Which tools and error types participate in tool_repeat_guard fuse."""

from __future__ import annotations

from app.agent.chat.tools.browser_names import BROWSER_TOOL_NAMES

REPEAT_GUARD_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "invalid_arguments",
        "invalid_json",
        "missing_required_arg",
        "invalid_argument_type",
        "unknown_tool",
        "parse_fatal",
        "sandbox_error",
        "sandbox_timeout",
    }
)


def is_repeat_guard_eligible(tool_name: str, error_class: str | None) -> bool:
    if tool_name in BROWSER_TOOL_NAMES:
        return False
    if error_class is None:
        return False
    return error_class in REPEAT_GUARD_ERROR_TYPES
