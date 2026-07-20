"""signal_browser_blocked: model's recognized dead-end exit for unbeatable verifications."""

from __future__ import annotations

from pathlib import Path

from app.chat.tools.result import BROWSER_BLOCKED, ToolResult


async def signal_browser_blocked(
    conversation_id: int,
    workspace: Path,
    label: str | None = None,
) -> ToolResult:
    """Signal that automation cannot continue; no screenshot or disk artifact."""
    _ = conversation_id, workspace, label
    return ToolResult(success=True, output="{}", error_type=BROWSER_BLOCKED)
