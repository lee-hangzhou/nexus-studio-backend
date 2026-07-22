"""Capture objective browser page state for ops debug (png under .browser/debug/)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import httpx

from app.agent.chat.browser import container_client, inprocess_session, runtime, session_manager
from app.agent.chat.browser.debug_capture import (
    debug_capture_path,
    is_allowed_debug_capture_path,
    prune_debug_captures,
)
from app.agent.chat.browser.session_lock import session_page_lock
from app.agent.chat.tools.result import BROWSER_ERROR, INVALID_ARGUMENTS, ToolResult
from app.agent.chat.workspace import resolve_workspace_file
from app.server.infra.logger import logger


def browser_state_asset_url(conversation_id: int, screenshot_path: str) -> str:
    return (
        f"/api/v1/chat/browser/state/asset?"
        f"conversation_id={conversation_id}&path={quote(screenshot_path)}"
    )


def _payload(
    *,
    url: str,
    title: str,
    text_preview: str,
    screenshot_path: str,
    conversation_id: int,
) -> dict[str, str]:
    return {
        "url": url,
        "title": title,
        "text_preview": text_preview,
        "screenshot_path": screenshot_path,
        "screenshot_url": browser_state_asset_url(conversation_id, screenshot_path),
    }


async def capture_browser_state(
    *,
    conversation_id: int,
    workspace: Path,
    label: str | None = None,
    full_page: bool = True,
    text_max_chars: int = 1200,
    turn_id: str | None = None,
) -> ToolResult:
    """Capture png snapshot and metadata for ops troubleshooting."""
    if text_max_chars < 0:
        return ToolResult.fail(INVALID_ARGUMENTS, detail="text_max_chars must be non-negative")

    workspace.mkdir(parents=True, exist_ok=True)
    prune_debug_captures(workspace)
    image_path, image_rel = debug_capture_path(workspace, label)
    if runtime.use_inprocess_runtime():
        result = await _capture_inprocess(
            conversation_id=conversation_id,
            image_path=image_path,
            image_rel=image_rel,
            full_page=full_page,
            text_max_chars=text_max_chars,
        )
    else:
        result = await _capture_container(
            conversation_id=conversation_id,
            workspace=workspace,
            image_path=image_path,
            image_rel=image_rel,
            full_page=full_page,
            text_max_chars=text_max_chars,
        )

    if result.success:
        try:
            data = json.loads(result.output)
            page_url = data.get("url")
        except json.JSONDecodeError:
            page_url = None
        logger.info(
            "browser_debug_capture",
            conversation_id=conversation_id,
            turn_id=turn_id,
            screenshot_path=image_rel,
            page_url=page_url,
        )
        prune_debug_captures(workspace)
    return result


async def _capture_inprocess(
    *,
    conversation_id: int,
    image_path: Path,
    image_rel: str,
    full_page: bool,
    text_max_chars: int,
) -> ToolResult:
    async with session_page_lock(conversation_id):
        page = await inprocess_session.get_page(conversation_id)
        try:
            text = await page.evaluate(
                """(limit) => (document.body?.innerText || "").slice(0, limit)""",
                text_max_chars,
            )
            await page.screenshot(path=str(image_path), full_page=full_page, type="png")
        except Exception as exc:
            return ToolResult.fail(BROWSER_ERROR, detail=str(exc))
        data = _payload(
            url=page.url,
            title=await page.title(),
            text_preview=str(text or ""),
            screenshot_path=image_rel,
            conversation_id=conversation_id,
        )
        return ToolResult.ok(json.dumps(data, ensure_ascii=False))


async def _capture_container(
    *,
    conversation_id: int,
    workspace: Path,
    image_path: Path,
    image_rel: str,
    full_page: bool,
    text_max_chars: int,
) -> ToolResult:
    async with session_page_lock(conversation_id):
        record = await session_manager.ensure_session(conversation_id, workspace)
        try:
            data = await container_client.capture_state(
                record,
                image_rel=image_rel,
                full_page=full_page,
                text_max_chars=text_max_chars,
                screenshot_url=browser_state_asset_url(conversation_id, image_rel),
            )
        except httpx.HTTPError as exc:
            await session_manager.invalidate_if_dead(conversation_id)
            return ToolResult.fail(BROWSER_ERROR, detail=str(exc))
        except RuntimeError as exc:
            return ToolResult.fail(BROWSER_ERROR, detail=str(exc))

    return ToolResult.ok(json.dumps(data, ensure_ascii=False))


async def read_browser_state_asset_bytes(*, workspace: Path, path: str) -> bytes:
    if not is_allowed_debug_capture_path(workspace, path):
        raise PermissionError("browser state asset path is not allowed")
    target = resolve_workspace_file(workspace, path)
    if not target.is_file():
        raise FileNotFoundError("browser state asset not found")
    return target.read_bytes()
