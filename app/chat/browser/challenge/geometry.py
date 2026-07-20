"""CSS viewport geometry for slider / drag challenges."""

from __future__ import annotations

from typing import Any

from app.core.config import settings

BBox = dict[str, float]

_CORRIDOR_EPS = 2.0


def compute_handle_travel_bounds(track: BBox, handle: BBox) -> dict[str, float]:
    t_left = float(track["x"])
    t_right = float(track["x"]) + float(track["width"])
    h_width = float(handle["width"])
    return {
        "left": t_left,
        "right": t_right - h_width,
        "top": float(handle["y"]),
        "bottom": float(handle["y"]) + float(handle["height"]),
    }


def compute_drag_corridor(track: BBox, handle: BBox) -> BBox:
    eps = _CORRIDOR_EPS
    t_left = float(track["x"])
    t_right = float(track["x"]) + float(track["width"])
    t_top = float(track["y"])
    t_bottom = float(track["y"]) + float(track["height"])
    h_top = float(handle["y"])
    h_bottom = float(handle["y"]) + float(handle["height"])
    top = min(t_top, h_top) - eps
    bottom = max(t_bottom, h_bottom) + eps
    return {
        "x": t_left - eps,
        "y": top,
        "width": (t_right + eps) - (t_left - eps),
        "height": bottom - top,
    }


def validate_slider_geometry(track: BBox, handle: BBox) -> str | None:
    travel = compute_handle_travel_bounds(track, handle)
    if travel["right"] < travel["left"]:
        return "handle wider than track travel range"
    h_left = float(handle["x"])
    t_left = float(track["x"])
    drift_ratio = settings.CHAT_CHALLENGE_BBOX_DRIFT_RATIO
    if abs(h_left - t_left) > max(float(track["width"]) * drift_ratio, 8.0):
        return "handle not near track left origin"
    h_cy = float(handle["y"]) + float(handle["height"]) / 2
    t_cy = float(track["y"]) + float(track["height"]) / 2
    if abs(h_cy - t_cy) > float(track["height"]) * 0.6:
        return "handle vertical misaligned with track"
    return None


def build_entities_payload(
    *,
    container: BBox,
    track: BBox,
    handle: BBox,
) -> dict[str, Any]:
    travel = compute_handle_travel_bounds(track, handle)
    return {
        "container": container,
        "track": track,
        "handle": handle,
        "drag_corridor": compute_drag_corridor(track, handle),
        "travel": travel,
    }


def _normalize_bbox(raw: dict[str, Any]) -> BBox:
    return {k: float(raw[k]) for k in ("x", "y", "width", "height")}


def parse_geometry_bboxes(data: dict[str, Any]) -> tuple[BBox, BBox, BBox]:
    container = data.get("container")
    track = data.get("track")
    handle = data.get("handle")
    if not isinstance(container, dict) or not isinstance(track, dict) or not isinstance(handle, dict):
        raise RuntimeError("challenge element not found")
    return _normalize_bbox(container), _normalize_bbox(track), _normalize_bbox(handle)


def build_scale_facts(
    track: BBox,
    *,
    content_width_px: float | None,
) -> dict[str, Any]:
    track_w = float(track["width"])
    css_per: float | None = None
    if content_width_px is not None and content_width_px > 0:
        css_per = track_w / content_width_px
    return {
        "content_space": "intrinsic_pixels" if content_width_px else "css_viewport",
        "track_width_css": track_w,
        "content_width_px": content_width_px,
        "css_per_intrinsic_x": css_per,
    }
