"""Ops debug page snapshots under workspace/.browser/debug/."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from app.server.infra.config import settings
from app.server.infra.logger import logger

DEBUG_FILE_PREFIX = "browser_state_"


def debug_capture_path(workspace: Path, label: str | None) -> tuple[Path, str]:
    root = workspace / ".browser" / "debug"
    root.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (label or "page"))
    stem = f"{DEBUG_FILE_PREFIX}{safe_label}_{uuid4().hex[:12]}"
    image_path = root / f"{stem}.png"
    return image_path, image_path.relative_to(workspace).as_posix()


def is_allowed_debug_capture_path(workspace: Path, path: str) -> bool:
    target = (workspace / path).resolve()
    try:
        rel = target.relative_to(workspace.resolve())
    except ValueError:
        return False
    if len(rel.parts) != 3:
        return False
    return (
        rel.parts[0] == ".browser"
        and rel.parts[1] == "debug"
        and rel.name.startswith(DEBUG_FILE_PREFIX)
        and rel.suffix.lower() == ".png"
    )


def prune_debug_captures(workspace: Path) -> int:
    """Delete expired and excess debug png files. Returns deleted count."""
    root = workspace / ".browser" / "debug"
    if not root.is_dir():
        return 0

    ttl = settings.CHAT_BROWSER_DEBUG_CAPTURE_TTL_SEC
    max_count = settings.CHAT_BROWSER_DEBUG_CAPTURE_MAX_COUNT
    now = time.time()

    files = sorted(
        (p for p in root.glob(f"{DEBUG_FILE_PREFIX}*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    deleted = 0
    for index, path in enumerate(files):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = ttl + 1
        over_ttl = ttl > 0 and age > ttl
        over_cap = max_count > 0 and index >= max_count
        if over_ttl or over_cap:
            try:
                path.unlink()
                deleted += 1
                logger.info(
                    "browser_debug_capture_pruned",
                    path=path.relative_to(workspace).as_posix(),
                    reason="ttl" if over_ttl else "max_count",
                )
            except OSError as exc:
                logger.warning(
                    "browser_debug_capture_prune_failed",
                    path=str(path),
                    error=str(exc),
                )
    return deleted
