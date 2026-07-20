"""browser_auth_status — read-only merged auth facts for a site."""

from __future__ import annotations

import json
from typing import Annotated

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.chat.browser import inprocess_session
from app.chat.browser import session_storage
from app.chat.gate import site_auth
from app.chat.tools.result import INVALID_ARGUMENTS, ToolResult


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


def _workspace_from_config(config: RunnableConfig | None):
    from pathlib import Path

    return Path((config.get("configurable") or {}).get("workspace") or "/tmp")


async def browser_auth_status(
    expected_domain: str | None = None,
    login_probe_selector: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Return structured auth_flags: storage, site_auth, and login probe (read-only)."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)

    domain: str | None = None
    if expected_domain:
        try:
            domain = session_storage.normalize_domain(expected_domain)
        except ValueError as exc:
            return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
    else:
        try:
            domain = await session_storage.resolve_domain(conversation_id, None)
        except ValueError:
            domain = None

    storage_path_exists = False
    storage_expired = False
    if domain:
        path = session_storage.storage_path(workspace, domain)
        storage_path_exists = path.is_file()
        if storage_path_exists:
            storage_expired = session_storage._storage_expired(path)

    site_state = await site_auth.get_site_auth(conversation_id, domain) if domain else None
    logged_in = await session_storage.probe_logged_in(
        conversation_id,
        login_probe_selector,
    )
    auth_flags = {
        "login_method_selected": bool(site_state and site_state.get("login_method_selected")),
        "credentials_submitted": bool(site_state and site_state.get("credentials_submitted")),
        "phone_otp_flow_started": bool(site_state and site_state.get("phone_otp_flow_started")),
        "storage_available": storage_path_exists and not storage_expired,
    }
    payload = {
        "domain": domain,
        "logged_in": logged_in,
        "need_login": (not logged_in) if logged_in is not None else None,
        "auth_flags": auth_flags,
        "site_auth": site_state,
    }
    return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
