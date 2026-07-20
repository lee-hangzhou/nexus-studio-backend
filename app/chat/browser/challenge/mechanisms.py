"""Challenge mechanism implementations (in-process and container driver)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.chat.browser.challenge import artifacts, geometry
from app.chat.browser.session_lock import session_page_lock
from app.core.logger import logger


async def read_geometry(
    *,
    conversation_id: int,
    workspace: Path,
    container_selector: str,
    track_selector: str,
    handle_selector: str,
    attempt_id: str | None = None,
    scope_selector: str | None = None,
    content_selector: str | None = None,
) -> dict[str, Any]:
    from app.chat.browser.challenge import runtime as challenge_runtime

    async with session_page_lock(conversation_id):
        data = await challenge_runtime.read_geometry(
            conversation_id=conversation_id,
            workspace=workspace,
            container_selector=container_selector,
            track_selector=track_selector,
            handle_selector=handle_selector,
            scope_selector=scope_selector,
            content_selector=content_selector,
        )
    container, track, handle = geometry.parse_geometry_bboxes(data)
    geom_err = geometry.validate_slider_geometry(track, handle)
    if geom_err:
        raise ValueError(f"challenge_geometry_invalid: {geom_err}")
    viewport = data.get("viewport") if isinstance(data.get("viewport"), dict) else {}
    frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    instruction_text = str(data.get("instruction_text") or "")
    content_width_px = data.get("content_width_px")
    intrinsic: float | None = None
    if isinstance(content_width_px, (int, float)) and content_width_px > 0:
        intrinsic = float(content_width_px)
    payload = {
        "coordinate_space": "css_viewport",
        "frame": frame,
        "viewport": viewport,
        "entities": geometry.build_entities_payload(container=container, track=track, handle=handle),
        "travel": geometry.compute_handle_travel_bounds(track, handle),
        "instruction_text": instruction_text,
        "scale": geometry.build_scale_facts(track, content_width_px=intrinsic),
    }
    resolved = data.get("resolved_index")
    if isinstance(resolved, dict):
        payload["resolved_index"] = resolved
    candidate_count = data.get("candidate_count")
    if isinstance(candidate_count, dict):
        payload["candidate_count"] = candidate_count
    artifacts.write_step(workspace, attempt_id or "", "01_frame", payload)
    return payload


async def screenshot_element(
    *,
    conversation_id: int,
    workspace: Path,
    selector: str,
    path: str,
    attempt_id: str | None = None,
    scope_selector: str | None = None,
) -> dict[str, Any]:
    from app.chat.browser.challenge import runtime as challenge_runtime

    rel = path.lstrip("/")
    out = workspace / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    async with session_page_lock(conversation_id):
        bbox_page = await challenge_runtime.screenshot_element(
            conversation_id=conversation_id,
            workspace=workspace,
            selector=selector,
            path_rel=rel,
            scope_selector=scope_selector,
        )
    width = float(bbox_page.get("width") or 0)
    height = float(bbox_page.get("height") or 0)
    payload = {
        "path": rel,
        "width": width,
        "height": height,
        "bbox_page": bbox_page,
        "coordinate_space": "css_viewport",
    }
    artifacts.write_step(workspace, attempt_id or "", "03_screenshot", payload)
    return payload


async def dispatch_pointer_trace(
    *,
    conversation_id: int,
    workspace: Path,
    events: list[dict[str, Any]],
    attempt_id: str | None = None,
) -> dict[str, Any]:
    from app.chat.browser.challenge import runtime as challenge_runtime

    if not events:
        raise ValueError("pointer_dispatch_failed: empty events")
    normalized: list[dict[str, Any]] = []
    for ev in events:
        ev_type = str(ev.get("type") or "")
        if ev_type not in {"down", "move", "up"}:
            raise ValueError(f"pointer_dispatch_failed: invalid event type {ev_type!r}")
        normalized.append(
            {
                "type": ev_type,
                "x": float(ev.get("x") or 0),
                "y": float(ev.get("y") or 0),
                "t": int(ev.get("t") or 0),
            }
        )
    downs = [e for e in normalized if e["type"] == "down"]
    ups = [e for e in normalized if e["type"] == "up"]
    if len(downs) != 1 or len(ups) != 1:
        raise ValueError("pointer_dispatch_failed: trace must have exactly one down and one up")
    started = time.monotonic()
    async with session_page_lock(conversation_id):
        await challenge_runtime.dispatch_pointer_trace(
            conversation_id=conversation_id,
            workspace=workspace,
            events=normalized,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    payload = {"events_emitted": len(normalized), "duration_ms": duration_ms}
    artifacts.write_step(workspace, attempt_id or "", "05_trace", {"events": normalized, **payload})
    logger.info(
        "challenge_pointer_trace_dispatched",
        conversation_id=conversation_id,
        events=len(normalized),
        duration_ms=duration_ms,
    )
    return payload


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
    attempt_id: str | None = None,
) -> dict[str, Any]:
    from app.chat.browser.challenge import runtime as challenge_runtime

    async with session_page_lock(conversation_id):
        result = await challenge_runtime.wait_probe(
            conversation_id=conversation_id,
            workspace=workspace,
            selector=selector,
            success_selector=success_selector,
            failure_selector=failure_selector,
            retry_text_probe=retry_text_probe,
            panel_selector=panel_selector,
            scope_selector=scope_selector,
            timeout_ms=timeout_ms,
        )
    artifacts.write_step(workspace, attempt_id or "", "07_verify", result)
    return result
