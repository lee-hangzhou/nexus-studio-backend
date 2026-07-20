"""browser_restore_session / browser_end_session tools."""

from __future__ import annotations

import json
from typing import Annotated

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.chat.browser import inprocess_session
from app.chat.browser import session_storage
from app.chat.browser.debug_capture import prune_debug_captures
from app.chat.gate.pending import get_gate_pending
from app.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.core.config import settings
from app.core.logger import logger


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


def _workspace_from_config(config: RunnableConfig | None):
    from pathlib import Path

    workspace_path = (config.get("configurable") or {}).get("workspace") or "/tmp"
    return Path(workspace_path)


async def browser_restore_session(
    expected_domain: str | None = None,
    login_probe_selector: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Load saved storage_state for same-site follow-up work."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)
    result = await session_storage.restore_session(
        conversation_id=conversation_id,
        workspace=workspace,
        expected_domain=expected_domain,
        login_probe_selector=login_probe_selector,
    )
    return result.to_tool_message()


async def browser_end_session(
    save: bool = True,
    expected_domain: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Save storage_state and close browser; forbidden while gate pending."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)

    pending = await get_gate_pending(conversation_id)
    if pending and str(pending.get("status") or "pending") == "pending":
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail="gate pending; do not end session while waiting on user gate",
        ).to_tool_message()

    saved = False
    domain: str | None = None
    if save and settings.CHAT_BROWSER_STORAGE_ENABLED:
        save_result = await session_storage.save_session(
            conversation_id=conversation_id,
            workspace=workspace,
            expected_domain=expected_domain,
        )
        if not save_result.success:
            return save_result.to_tool_message()
        try:
            payload = json.loads(save_result.output)
            saved = bool(payload.get("saved"))
            domain = payload.get("domain")
        except json.JSONDecodeError:
            saved = save_result.success

    had_session = await inprocess_session.get_session_entry(conversation_id) is not None
    await inprocess_session.close_session(conversation_id)
    prune_debug_captures(workspace)

    logger.info(
        "browser_session_ended",
        conversation_id=conversation_id,
        saved=saved,
        domain=domain,
        had_session=had_session,
    )
    return ToolResult.ok(
        json.dumps({"status": "session_closed", "saved": saved, "domain": domain})
    ).to_tool_message()
