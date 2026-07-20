"""Skill bundle hash and workspace session materialization."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.chat.skills.registry import SKILLS_DIR, SkillRegistry

_SKIP_DIRS = frozenset({"__pycache__", ".git"})
_SKIP_FILES = frozenset({".DS_Store"})


def compute_bundle_hash() -> str:
    """Hash only first-level skill subdirectories; skip root-level files."""
    parts: list[str] = []
    if not SKILLS_DIR.is_dir():
        return hashlib.sha256(b"").hexdigest()

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").is_file():
            continue
        skill_name = skill_dir.name
        for file_path in sorted(skill_dir.rglob("*")):
            if file_path.is_dir():
                continue
            if file_path.name in _SKIP_FILES:
                continue
            if any(part in _SKIP_DIRS for part in file_path.relative_to(skill_dir).parts[:-1]):
                continue
            rel = file_path.relative_to(skill_dir).as_posix()
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            parts.append(f"{skill_name}/{rel}\0{digest}")

    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_manifest(workspace: Path) -> dict:
    path = workspace / ".skills_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_manifest(workspace: Path, manifest: dict) -> None:
    path = workspace / ".skills_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _skills_materialized(workspace: Path, skill_names: list[str]) -> bool:
    for name in skill_names:
        if not (workspace / "skills" / name / "SKILL.md").is_file():
            return False
    return True


def ensure_workspace_session(workspace: Path) -> None:
    """Idempotently copy bundled skills into workspace/skills/."""
    entries = SkillRegistry.load()
    skill_names = [entry.name for entry in entries]
    bundle_hash = compute_bundle_hash()
    manifest = _load_manifest(workspace)
    stored_hash = manifest.get("bundle_hash")

    if stored_hash == bundle_hash and _skills_materialized(workspace, skill_names):
        return

    skills_dest = workspace / "skills"
    if skills_dest.exists():
        shutil.rmtree(skills_dest)
    skills_dest.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        src = SKILLS_DIR / entry.name
        dest = skills_dest / entry.name
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))

    _save_manifest(
        workspace,
        {
            "bundle_hash": bundle_hash,
            "skill_names": skill_names,
            "materialized_at": datetime.now(timezone.utc).isoformat(),
        },
    )
