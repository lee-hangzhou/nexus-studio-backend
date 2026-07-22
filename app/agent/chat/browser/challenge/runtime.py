"""Dual-runtime entry for challenge mechanisms (in-process vs container driver)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent.chat.browser import container_client, inprocess_session, runtime as browser_runtime, session_manager
from app.agent.chat.browser.challenge import locators, probe


async def _get_page(conversation_id: int, workspace: Path):
    if browser_runtime.use_inprocess_runtime():
        return await inprocess_session.get_page(conversation_id)
    record = await session_manager.ensure_session(conversation_id, workspace)
    return record


async def _intrinsic_content_width(page: Any, selector: str, *, scope_selector: str | None) -> float | None:
    try:
        loc = (
            page.locator(scope_selector).locator(selector)
            if scope_selector
            else page.locator(selector)
        )
        value = await loc.first.evaluate(
            """el => {
                if (el.naturalWidth) return el.naturalWidth;
                if (el.width && el.tagName === 'CANVAS') return el.width;
                return null;
            }"""
        )
        if value is None:
            return None
        width = float(value)
        return width if width > 0 else None
    except Exception:
        return None


async def read_geometry(
    *,
    conversation_id: int,
    workspace: Path,
    container_selector: str,
    track_selector: str,
    handle_selector: str,
    scope_selector: str | None = None,
    content_selector: str | None = None,
) -> dict[str, Any]:
    if browser_runtime.use_inprocess_runtime():
        page = await inprocess_session.get_page(conversation_id)
        container, c_idx, c_count = await locators.resolve_visible_bbox(
            page, container_selector, scope_selector=scope_selector
        )
        track, t_idx, t_count = await locators.resolve_visible_bbox(
            page, track_selector, scope_selector=scope_selector
        )
        handle, h_idx, h_count = await locators.resolve_visible_bbox(
            page, handle_selector, scope_selector=scope_selector
        )
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        instruction = ""
        try:
            root = (
                page.locator(scope_selector).locator(container_selector).nth(c_idx)
                if scope_selector
                else page.locator(container_selector).nth(c_idx)
            )
            instruction = (await root.inner_text())[:500]
        except Exception:
            pass
        content_sel = content_selector or track_selector
        content_width_px = await _intrinsic_content_width(
            page, content_sel, scope_selector=scope_selector
        )
        return {
            "container": container,
            "track": track,
            "handle": handle,
            "viewport": dict(viewport),
            "frame": {"x": 0, "y": 0, "width": viewport["width"], "height": viewport["height"]},
            "instruction_text": instruction,
            "resolved_index": {"container": c_idx, "track": t_idx, "handle": h_idx},
            "candidate_count": {"container": c_count, "track": t_count, "handle": h_count},
            "content_width_px": content_width_px,
        }
    record = await session_manager.ensure_session(conversation_id, workspace)
    return await container_client.challenge_read_geometry(
        record,
        container_selector=container_selector,
        track_selector=track_selector,
        handle_selector=handle_selector,
        scope_selector=scope_selector,
        content_selector=content_selector,
    )


async def screenshot_element(
    *,
    conversation_id: int,
    workspace: Path,
    selector: str,
    path_rel: str,
    scope_selector: str | None = None,
) -> dict[str, float]:
    if browser_runtime.use_inprocess_runtime():
        page = await inprocess_session.get_page(conversation_id)
        loc = await locators.resolve_visible_locator(
            page, selector, scope_selector=scope_selector
        )
        bbox = await loc.bounding_box()
        if not bbox:
            raise RuntimeError("challenge element not found")
        out = workspace / path_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        await loc.screenshot(path=str(out), type="png")
        return {k: float(bbox[k]) for k in ("x", "y", "width", "height")}
    record = await session_manager.ensure_session(conversation_id, workspace)
    data = await container_client.challenge_screenshot_element(
        record,
        selector=selector,
        path_rel=path_rel,
        scope_selector=scope_selector,
    )
    bbox = data.get("bbox_page")
    if not isinstance(bbox, dict):
        raise RuntimeError("challenge element not found")
    return {k: float(bbox[k]) for k in ("x", "y", "width", "height")}


async def dispatch_pointer_trace(
    *,
    conversation_id: int,
    workspace: Path,
    events: list[dict[str, Any]],
) -> None:
    if browser_runtime.use_inprocess_runtime():
        page = await inprocess_session.get_page(conversation_id)
        prev_t = 0
        for ev in events:
            delay = int(ev.get("t") or 0) - prev_t
            if delay > 0:
                await page.wait_for_timeout(delay)
            prev_t = int(ev.get("t") or 0)
            x = float(ev["x"])
            y = float(ev["y"])
            ev_type = str(ev["type"])
            if ev_type == "down":
                await page.mouse.move(x, y)
                await page.mouse.down()
            elif ev_type == "move":
                await page.mouse.move(x, y)
            elif ev_type == "up":
                await page.mouse.move(x, y)
                await page.mouse.up()
        return
    record = await session_manager.ensure_session(conversation_id, workspace)
    await container_client.challenge_dispatch_pointer_trace(record, events=events)


async def wait_probe(
    *,
    conversation_id: int,
    workspace: Path,
    selector: str | None = None,
    success_selector: str | None = None,
    failure_selector: str | None = None,
    retry_text_probe: str | None = None,
    panel_selector: str | None = None,
    scope_selector: str | None = None,
    timeout_ms: int,
) -> dict[str, Any]:
    resolved_success = success_selector or selector
    if not resolved_success:
        raise ValueError("wait_probe requires success_selector or selector")

    if browser_runtime.use_inprocess_runtime():
        page = await inprocess_session.get_page(conversation_id)
        return await probe.observe_challenge_probe(
            page,
            success_selector=resolved_success,
            failure_selector=failure_selector,
            retry_text_probe=retry_text_probe,
            panel_selector=panel_selector,
            scope_selector=scope_selector,
            timeout_ms=timeout_ms,
        )
    record = await session_manager.ensure_session(conversation_id, workspace)
    return await container_client.challenge_wait_probe(
        record,
        selector=resolved_success,
        success_selector=resolved_success,
        failure_selector=failure_selector,
        retry_text_probe=retry_text_probe,
        panel_selector=panel_selector,
        scope_selector=scope_selector,
        timeout_ms=timeout_ms,
    )
