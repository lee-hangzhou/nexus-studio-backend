"""Gate asset capture and serve helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.agent.chat.browser import runtime as browser_runtime
from app.agent.chat.browser import session_manager
from app.agent.chat.browser.session_lock import session_page_lock
from app.agent.chat.gate import meta as gate_meta
from app.agent.chat.gate.qr_verify import GateCaptureError, qr_verify_output, verify_png_file
from app.agent.chat.tools.result import QR_ELEMENT_NOT_VISIBLE, ToolResult
from app.server.infra.config import settings
from app.server.infra.logger import logger


def gate_asset_path(workspace: Path, gate_id: str, kind: str = "legacy") -> Path:
    return workspace / "raw" / f"gate_{gate_id}_{kind}.png" if kind != "legacy" else workspace / "raw" / f"gate_{gate_id}.png"


def gate_asset_tmp_path(workspace: Path, gate_id: str) -> Path:
    return workspace / "raw" / f"gate_{gate_id}.png.tmp"


def gate_asset_url(conversation_id: int, gate_id: str, kind: str | None = None) -> str:
    base = f"/api/v1/chat/gate/asset?conversation_id={conversation_id}&gate_id={gate_id}"
    if kind:
        return f"{base}&kind={kind}"
    return base


def build_assets_payload(
    *,
    conversation_id: int,
    gate_id: str,
    asset_kind: str,
    refresh_interval_sec: int,
) -> dict[str, object]:
    if asset_kind == "challenge":
        return {
            "challenge_image_url": gate_asset_url(conversation_id, gate_id),
            "refresh_interval_sec": refresh_interval_sec,
        }
    key = "qr_image_url" if asset_kind == "qr" else "captcha_image_url"
    return {
        key: gate_asset_url(conversation_id, gate_id),
        "refresh_interval_sec": refresh_interval_sec,
    }


async def _atomic_write_png(path: Path, write_fn) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    await write_fn(tmp)
    if not tmp.is_file():
        raise RuntimeError("gate asset screenshot file missing")
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    logger.info("asset_screenshot_created", path=str(path))
    return path


async def preflight_qr_locator(*, conversation_id: int, image_selector: str) -> None:
    min_size = settings.CHAT_QR_MIN_BBOX_SIZE
    bbox = await browser_runtime.locator_bounding_box(
        conversation_id,
        image_selector,
        workspace=workspace,
    )
    if bbox is None:
        raise GateCaptureError(
            QR_ELEMENT_NOT_VISIBLE,
            json.dumps({"selector": image_selector, "reason": "no_bbox"}, ensure_ascii=False),
        )
    width = float(bbox.get("width") or 0)
    height = float(bbox.get("height") or 0)
    short_side = min(width, height)
    if short_side < min_size:
        raise GateCaptureError(
            QR_ELEMENT_NOT_VISIBLE,
            json.dumps(
                {
                    "selector": image_selector,
                    "bbox_width": width,
                    "bbox_height": height,
                    "min_bbox_size": min_size,
                },
                ensure_ascii=False,
            ),
        )


def _verify_qr_file(path: Path) -> str:
    result = verify_png_file(path)
    return qr_verify_output(result)


async def capture_gate_image(
    *,
    conversation_id: int,
    workspace: Path,
    gate_id: str,
    image_selector: str,
    asset_kind: str | None = None,
) -> tuple[Path, str | None]:
    workspace.mkdir(parents=True, exist_ok=True)
    out = gate_asset_path(workspace, gate_id)

    if asset_kind == "qr":
        await preflight_qr_locator(conversation_id=conversation_id, image_selector=image_selector)

    async def _write(tmp: Path) -> None:
        if browser_runtime.use_inprocess_runtime():
            from app.agent.chat.browser import inprocess_session

            async with session_page_lock(conversation_id):
                page = await inprocess_session.get_page(conversation_id)
                await page.locator(image_selector).screenshot(path=str(tmp), type="png")
        else:
            rel = tmp.relative_to(workspace).as_posix()
            from app.agent.chat.browser import container_client

            async with session_page_lock(conversation_id):
                record = await session_manager.ensure_session(conversation_id, workspace)
                await container_client.locator_screenshot(
                    record,
                    path_rel=rel,
                    selector=image_selector,
                )

    path = await _atomic_write_png(out, _write)
    verify_suffix: str | None = None
    if asset_kind == "qr":
        verify_suffix = _verify_qr_file(path)
    return path, verify_suffix


async def refresh_gate_asset(
    *,
    conversation_id: int,
    workspace: Path,
    gate_id: str,
) -> Path:
    meta = await gate_meta.get_gate_meta(gate_id)
    if meta is None:
        raise FileNotFoundError("gate meta not found")
    if int(meta.get("conversation_id") or 0) != conversation_id:
        raise PermissionError("gate conversation mismatch")
    image_selector = str(meta.get("image_selector") or "")
    if not image_selector:
        raise FileNotFoundError("gate image selector missing")
    asset_kind = str(meta.get("asset_kind") or "")
    path, _ = await capture_gate_image(
        conversation_id=conversation_id,
        workspace=workspace,
        gate_id=gate_id,
        image_selector=image_selector,
        asset_kind=asset_kind or None,
    )
    return path


def read_gate_asset_file(workspace: Path, gate_id: str, kind: str = "legacy") -> bytes:
    path = gate_asset_path(workspace, gate_id, kind)
    if not path.is_file():
        raise FileNotFoundError("gate asset file missing")
    return path.read_bytes()


def delete_gate_assets(workspace: Path, gate_id: str) -> list[str]:
    """Remove gate png/tmp files once the gate no longer has consumers."""
    if not gate_id:
        return []
    raw_dir = workspace / "raw"
    candidates: list[Path] = [
        gate_asset_path(workspace, gate_id),
        gate_asset_tmp_path(workspace, gate_id),
    ]
    if raw_dir.is_dir():
        candidates.extend(raw_dir.glob(f"gate_{gate_id}_*.png"))
    deleted: list[str] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        path.unlink()
        deleted.append(rel)
        logger.info("gate_asset_deleted", gate_id=gate_id, path=rel)
    return deleted
