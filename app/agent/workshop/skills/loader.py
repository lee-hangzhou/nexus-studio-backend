from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

_SKILLS_ROOT = Path(__file__).resolve().parent


def load_workshop_skill_bodies(skill_refs: Sequence[str]) -> str:
    """读取 app/agent/workshop/skills/{ref}/SKILL.md 正文并拼接"""
    parts: list[str] = []
    for ref in skill_refs:
        skill_path = _SKILLS_ROOT / ref / "SKILL.md"
        if not skill_path.is_file():
            raise FileNotFoundError(f"workshop skill not found: {ref}")
        body = skill_path.read_text(encoding="utf-8").strip()
        if body:
            parts.append(f"### {ref}\n{body}")
    if not parts:
        return ""
    return "## 专家 Skill\n\n" + "\n\n".join(parts)
