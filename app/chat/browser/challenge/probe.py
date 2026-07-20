"""Multi-signal challenge probe observation (mechanism layer)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.chat.contracts.interaction import ProbeOutcome


async def _is_visible(page: Any, selector: str | None, *, scope_selector: str | None = None) -> bool:
    if not selector:
        return False
    try:
        if scope_selector:
            loc = page.locator(scope_selector).locator(selector)
        else:
            loc = page.locator(selector)
        if await loc.count() == 0:
            return False
        return await loc.first.is_visible()
    except Exception:
        return False


async def _panel_dismissed(page: Any, panel_selector: str | None, *, scope_selector: str | None = None) -> bool:
    if not panel_selector:
        return False
    try:
        if scope_selector:
            loc = page.locator(scope_selector).locator(panel_selector)
        else:
            loc = page.locator(panel_selector)
        if await loc.count() == 0:
            return True
        box = await loc.first.bounding_box()
        if box is None:
            return True
        return float(box.get("width") or 0) <= 0 or float(box.get("height") or 0) <= 0
    except Exception:
        return True


async def _retry_text_visible(page: Any, retry_text_probe: str | None) -> bool:
    if not retry_text_probe:
        return False
    try:
        loc = page.get_by_text(retry_text_probe, exact=False)
        return await loc.first.is_visible()
    except Exception:
        return False


async def observe_challenge_probe(
    page: Any,
    *,
    success_selector: str | None,
    failure_selector: str | None = None,
    retry_text_probe: str | None = None,
    panel_selector: str | None = None,
    scope_selector: str | None = None,
    timeout_ms: int,
    poll_interval_ms: int = 200,
) -> dict[str, Any]:
    """Poll page until outcome is determined or timeout."""
    url_before = page.url
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    last_observations: dict[str, Any] = {
        "success_visible": False,
        "failure_visible": False,
        "retry_text_visible": False,
        "panel_dismissed": False,
    }
    error: str | None = None

    while time.monotonic() < deadline:
        success_visible = await _is_visible(
            page, success_selector, scope_selector=scope_selector
        )
        failure_visible = await _is_visible(
            page, failure_selector, scope_selector=scope_selector
        )
        retry_visible = await _retry_text_visible(page, retry_text_probe)
        panel_dismissed = await _panel_dismissed(
            page, panel_selector, scope_selector=scope_selector
        )
        last_observations = {
            "success_visible": success_visible,
            "failure_visible": failure_visible,
            "retry_text_visible": retry_visible,
            "panel_dismissed": panel_dismissed,
        }

        if success_visible:
            return _build_probe_result(
                outcome=ProbeOutcome.PASSED.value,
                verified=True,
                observations=last_observations,
                url_before=url_before,
                url_after=page.url,
                selector=success_selector or "",
                error=None,
            )
        if failure_visible or retry_visible:
            return _build_probe_result(
                outcome=ProbeOutcome.FAILED.value,
                verified=False,
                observations=last_observations,
                url_before=url_before,
                url_after=page.url,
                selector=success_selector or "",
                error="failure signal observed",
            )
        if panel_dismissed and panel_selector:
            return _build_probe_result(
                outcome=ProbeOutcome.DISMISSED.value,
                verified=False,
                observations=last_observations,
                url_before=url_before,
                url_after=page.url,
                selector=success_selector or "",
                error="challenge panel dismissed",
            )

        await asyncio.sleep(poll_interval_ms / 1000.0)

    return _build_probe_result(
        outcome=ProbeOutcome.INCONCLUSIVE.value,
        verified=False,
        observations=last_observations,
        url_before=url_before,
        url_after=page.url,
        selector=success_selector or "",
        error=error or "probe timeout",
    )


def _build_probe_result(
    *,
    outcome: str,
    verified: bool,
    observations: dict[str, Any],
    url_before: str,
    url_after: str,
    selector: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "verified": verified,
        "visible": observations.get("success_visible", False),
        "observations": observations,
        "url_before": url_before,
        "url_after": url_after,
        "selector": selector,
        "error": error,
    }
