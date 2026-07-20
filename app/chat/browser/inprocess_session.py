"""In-process Playwright session store (spike-only; wave3 migrates to container exec)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.core.config import settings

_lock = asyncio.Lock()
_playwright: Playwright | None = None
_sessions: dict[int, _SessionEntry] = {}


@dataclass
class _SessionEntry:
    context: BrowserContext
    page: Page


async def _ensure_playwright() -> Playwright:
    global _playwright
    if _playwright is None:
        _playwright = await async_playwright().start()
    return _playwright


async def get_page(conversation_id: int) -> Page:
    async with _lock:
        entry = _sessions.get(conversation_id)
        if entry is not None:
            return entry.page
        return await _create_session(conversation_id, storage_state_path=None)


async def _create_session(
    conversation_id: int,
    *,
    storage_state_path: Path | None,
) -> Page:
    pw = await _ensure_playwright()
    browser: Browser = await pw.chromium.launch(
        headless=not settings.CHAT_BROWSER_HEADED,
        slow_mo=settings.CHAT_BROWSER_SLOW_MO_MS or None,
    )
    context_kwargs: dict[str, Any] = {}
    if storage_state_path is not None and storage_state_path.is_file():
        context_kwargs["storage_state"] = str(storage_state_path)
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    _sessions[conversation_id] = _SessionEntry(context=context, page=page)
    return page


async def recreate_with_storage(conversation_id: int, storage_state_path: Path) -> Page:
    async with _lock:
        entry = _sessions.pop(conversation_id, None)
    if entry is not None:
        await entry.context.close()
        browser = entry.context.browser
        if browser is not None:
            await browser.close()
    async with _lock:
        return await _create_session(conversation_id, storage_state_path=storage_state_path)


async def get_session_entry(conversation_id: int) -> _SessionEntry | None:
    return _sessions.get(conversation_id)


async def close_session(conversation_id: int) -> None:
    async with _lock:
        entry = _sessions.pop(conversation_id, None)
    if entry is None:
        return
    await entry.context.close()
    browser = entry.context.browser
    if browser is not None:
        await browser.close()


async def shutdown_all() -> None:
    global _playwright
    async with _lock:
        ids = list(_sessions.keys())
    for cid in ids:
        await close_session(cid)
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


def session_snapshot_for_test() -> dict[int, Any]:
    """Test helper: identity of page objects per conversation."""
    return {cid: id(entry.page) for cid, entry in _sessions.items()}
