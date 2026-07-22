"""Debug artifact manifest for captcha automation attempts."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.server.infra.config import settings
from app.server.infra.logger import logger


def artifacts_enabled() -> bool:
    return settings.CHAT_CAPTCHA_DEBUG_ARTIFACTS


def attempt_dir(workspace: Path, attempt_id: str) -> Path:
    return workspace / "raw" / "captcha" / attempt_id


def write_step(
    workspace: Path,
    attempt_id: str,
    step_name: str,
    payload: dict[str, Any],
) -> None:
    if not artifacts_enabled() or not attempt_id:
        return
    root = attempt_dir(workspace, attempt_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    steps: list[str] = list(manifest.get("steps") or [])
    if step_name not in steps:
        steps.append(step_name)
    manifest["attempt_id"] = attempt_id
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    manifest["steps"] = steps
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / f"{step_name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cleanup_attempt(workspace: Path, attempt_id: str) -> None:
    """Remove attempt debug directory once the challenge chain ends."""
    if not attempt_id:
        return
    root = attempt_dir(workspace, attempt_id)
    if not root.is_dir():
        return
    rel = root.relative_to(workspace).as_posix()
    shutil.rmtree(root)
    logger.info("captcha_attempt_cleaned", attempt_id=attempt_id, path=rel)
