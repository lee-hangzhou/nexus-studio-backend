from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _manifest_path(workspace: Path) -> Path:
    return workspace / "attachments" / ".materialize_manifest.json"


def load_manifest(workspace: Path) -> dict[str, Any]:
    path = _manifest_path(workspace)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_manifest(workspace: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def manifest_key(attachment_id: int, file_sha256: str | None) -> str:
    return f"{attachment_id}:{file_sha256 or ''}"


def is_cache_hit(workspace: Path, attachment_id: int, file_sha256: str | None, safe_name: str) -> bool:
    manifest = load_manifest(workspace)
    entry = manifest.get(manifest_key(attachment_id, file_sha256))
    if not entry:
        return False
    binary_rel = entry.get("binary_rel") or f"attachments/{safe_name}"
    return (workspace / binary_rel).exists()


def record_materialized(
    workspace: Path,
    *,
    attachment_id: int,
    file_sha256: str | None,
    binary_rel: str,
) -> None:
    manifest = load_manifest(workspace)
    manifest[manifest_key(attachment_id, file_sha256)] = {
        "binary_rel": binary_rel,
    }
    save_manifest(workspace, manifest)
