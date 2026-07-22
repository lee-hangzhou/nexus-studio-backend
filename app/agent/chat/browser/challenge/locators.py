"""Visible locator resolution for challenge DOM (strict-mode safe)."""

from __future__ import annotations

from typing import Any

BBox = dict[str, float]

_MIN_VISIBLE_SIZE = 1.0


def _scoped_locator(page: Any, selector: str, *, scope_selector: str | None):
    if scope_selector:
        return page.locator(scope_selector).locator(selector)
    return page.locator(selector)


async def resolve_visible_bbox(
    page: Any,
    selector: str,
    *,
    scope_selector: str | None = None,
    min_width: float = _MIN_VISIBLE_SIZE,
    min_height: float = _MIN_VISIBLE_SIZE,
) -> tuple[BBox, int, int]:
    """Return (bbox, resolved_index, candidate_count) for first visible matching node."""
    loc = _scoped_locator(page, selector, scope_selector=scope_selector)
    count = await loc.count()
    if count == 0:
        raise RuntimeError("challenge element not found")

    for index in range(count):
        candidate = loc.nth(index)
        try:
            visible = await candidate.is_visible()
        except Exception:
            visible = False
        if not visible:
            continue
        box = await candidate.bounding_box()
        if not box:
            continue
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        if width < min_width or height < min_height:
            continue
        bbox: BBox = {k: float(box[k]) for k in ("x", "y", "width", "height")}
        return bbox, index, count

    raise RuntimeError("challenge element not found")


async def resolve_visible_locator(page: Any, selector: str, *, scope_selector: str | None = None):
    """Return Playwright Locator for the first visible match."""
    loc = _scoped_locator(page, selector, scope_selector=scope_selector)
    count = await loc.count()
    for index in range(count):
        candidate = loc.nth(index)
        try:
            if await candidate.is_visible():
                box = await candidate.bounding_box()
                if box and float(box.get("width") or 0) >= _MIN_VISIBLE_SIZE:
                    return candidate
        except Exception:
            continue
    raise RuntimeError("challenge element not found")
