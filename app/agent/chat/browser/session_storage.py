"""Playwright storage_state persistence under workspace/.browser/storage/."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.agent.chat.browser import container_client, inprocess_session, session_manager
from app.agent.chat.gate import site_auth
from app.agent.chat.tools.result import BROWSER_ERROR, INVALID_ARGUMENTS, ToolResult
from app.server.infra.config import settings
from app.server.infra.logger import logger


def normalize_domain(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        raise ValueError("empty domain")
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"invalid domain: {value}")
    return host


def storage_dir(workspace: Path) -> Path:
    return workspace / ".browser" / "storage"


def storage_path(workspace: Path, domain: str) -> Path:
    host = normalize_domain(domain)
    return storage_dir(workspace) / f"{host}.json"


def _storage_expired(path: Path) -> bool:
    ttl = int(settings.CHAT_BROWSER_STORAGE_TTL_SEC)
    if ttl <= 0:
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    return age > ttl


async def resolve_domain(conversation_id: int, expected_domain: str | None) -> str:
    if expected_domain:
        return normalize_domain(expected_domain)
    if settings.CHAT_BROWSER_INPROCESS:
        entry = await inprocess_session.get_session_entry(conversation_id)
        if entry is None:
            raise ValueError("expected_domain required when no active browser session")
        url = entry.page.url or ""
    else:
        record = await session_manager.get_existing_session(conversation_id)
        if record is None:
            raise ValueError("expected_domain required when no active browser session")
        info = await container_client.session_info(record)
        if not info.get("active"):
            raise ValueError("expected_domain required when no active browser session")
        url = str(info.get("url") or "")
    if not url or url == "about:blank":
        raise ValueError("expected_domain required when page has no URL")
    return normalize_domain(url)


async def probe_logged_in(
    conversation_id: int,
    login_probe_selector: str | None,
    *,
    timeout_ms: int | None = None,
) -> bool | None:
    if not login_probe_selector:
        return None
    timeout = timeout_ms if timeout_ms is not None else settings.CHAT_LOGIN_PROBE_TIMEOUT_MS
    if settings.CHAT_BROWSER_INPROCESS:
        entry = await inprocess_session.get_session_entry(conversation_id)
        if entry is None:
            return False
        try:
            loc = entry.page.locator(login_probe_selector)
            await loc.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False
    record = await session_manager.get_existing_session(conversation_id)
    if record is None:
        return False
    try:
        return await container_client.probe_logged_in(
            record,
            selector=login_probe_selector,
            timeout_ms=timeout,
        )
    except Exception:
        return False


async def save_session(
    *,
    conversation_id: int,
    workspace: Path,
    expected_domain: str | None = None,
) -> ToolResult:
    if not settings.CHAT_BROWSER_STORAGE_ENABLED:
        return ToolResult.ok(json.dumps({"saved": False, "reason": "storage_disabled"}))

    entry = None
    record = None
    if settings.CHAT_BROWSER_INPROCESS:
        entry = await inprocess_session.get_session_entry(conversation_id)
        if entry is None:
            return ToolResult.fail(BROWSER_ERROR, detail="no active browser session to save")
    else:
        record = await session_manager.get_existing_session(conversation_id)
        if record is None:
            return ToolResult.fail(BROWSER_ERROR, detail="no active browser session to save")

    try:
        domain = await resolve_domain(conversation_id, expected_domain)
    except ValueError as exc:
        return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc))

    target = storage_path(workspace, domain)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if settings.CHAT_BROWSER_INPROCESS:
            assert entry is not None
            await entry.context.storage_state(path=str(target))
        else:
            assert record is not None
            await container_client.save_storage(
                record,
                path_rel=str(target.relative_to(workspace)),
            )
    except Exception as exc:
        logger.warning(
            "browser_session_save_failed",
            conversation_id=conversation_id,
            domain=domain,
            error=str(exc),
        )
        return ToolResult.fail(BROWSER_ERROR, detail=str(exc))

    logger.info(
        "browser_session_saved",
        conversation_id=conversation_id,
        domain=domain,
    )
    return ToolResult.ok(
        json.dumps({"saved": True, "domain": domain, "path": str(target.relative_to(workspace))})
    )


async def restore_session(
    *,
    conversation_id: int,
    workspace: Path,
    expected_domain: str | None = None,
    login_probe_selector: str | None = None,
) -> ToolResult:
    domain: str | None = None
    payload: dict[str, Any]
    if expected_domain:
        try:
            domain = normalize_domain(expected_domain)
        except ValueError as exc:
            return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc))
    else:
        if settings.CHAT_BROWSER_INPROCESS:
            has_session = await inprocess_session.get_session_entry(conversation_id) is not None
        else:
            has_session = await session_manager.get_existing_session(conversation_id) is not None
        if has_session:
            try:
                domain = await resolve_domain(conversation_id, None)
            except ValueError:
                domain = None

    if not settings.CHAT_BROWSER_STORAGE_ENABLED:
        payload = {
            "restored": False,
            "logged_in": False,
            "need_login": True,
            "domain": domain,
            "reason": "storage_disabled",
        }
        return ToolResult.ok(json.dumps(payload))

    if domain is None:
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail="expected_domain required when no active session URL",
        )

    path = storage_path(workspace, domain)
    if not path.is_file() or _storage_expired(path):
        if path.is_file() and _storage_expired(path):
            logger.info(
                "browser_session_storage_expired",
                conversation_id=conversation_id,
                domain=domain,
            )
        payload = {
            "restored": False,
            "logged_in": False,
            "need_login": True,
            "domain": domain,
        }
        return ToolResult.ok(json.dumps(payload))

    try:
        if settings.CHAT_BROWSER_INPROCESS:
            await inprocess_session.recreate_with_storage(conversation_id, path)
        else:
            record = await session_manager.ensure_session(conversation_id, workspace)
            await container_client.restore_storage(
                record,
                path_rel=str(path.relative_to(workspace)),
            )
    except Exception as exc:
        logger.warning(
            "browser_session_restore_failed",
            conversation_id=conversation_id,
            domain=domain,
            error=str(exc),
        )
        return ToolResult.fail(BROWSER_ERROR, detail=str(exc))

    logged_in = await probe_logged_in(conversation_id, login_probe_selector)
    site_state = await site_auth.get_site_auth(conversation_id, domain) if domain else None
    auth_flags = {
        "login_method_selected": bool(site_state and site_state.get("login_method_selected")),
        "credentials_submitted": bool(site_state and site_state.get("credentials_submitted")),
        "phone_otp_flow_started": bool(site_state and site_state.get("phone_otp_flow_started")),
    }
    payload = {
        "restored": True,
        "logged_in": logged_in,
        "need_login": (not logged_in) if logged_in is not None else None,
        "domain": domain,
        "auth_flags": auth_flags,
    }
    logger.info(
        "browser_session_restored",
        conversation_id=conversation_id,
        domain=domain,
        logged_in=logged_in,
    )
    return ToolResult.ok(json.dumps(payload))
