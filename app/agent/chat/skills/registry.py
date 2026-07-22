"""Bundled skill registry — loaded once at application startup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    skill_md_path: Path


class SkillRegistry:
    _entries: list[SkillEntry] | None = None

    @classmethod
    def load(cls) -> list[SkillEntry]:
        if cls._entries is not None:
            return cls._entries

        entries: list[SkillEntry] = []
        if not SKILLS_DIR.is_dir():
            cls._entries = entries
            return entries

        for child in sorted(SKILLS_DIR.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            entry = cls._parse_skill_md(skill_md, expected_name=child.name)
            entries.append(entry)

        cls._entries = entries
        return entries

    @classmethod
    def reset(cls) -> None:
        cls._entries = None

    @classmethod
    def _parse_frontmatter(cls, text: str) -> dict[str, str]:
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise RuntimeError("Skill frontmatter missing or malformed")
        meta: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            value = raw.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            meta[key.strip()] = value
        return meta

    @classmethod
    def _parse_skill_md(cls, path: Path, *, expected_name: str) -> SkillEntry:
        text = path.read_text(encoding="utf-8")
        meta = cls._parse_frontmatter(text)

        name = meta.get("name")
        description = meta.get("description")
        if not name or not description:
            raise RuntimeError(f"Skill frontmatter requires name and description at {path}")
        if name != expected_name:
            raise RuntimeError(
                f"Skill name '{name}' does not match directory '{expected_name}' at {path}"
            )

        return SkillEntry(
            name=name,
            description=description.strip(),
            skill_md_path=path,
        )

    @classmethod
    def build_index_block(cls) -> str:
        entries = cls.load()
        if not entries:
            return ""
        lines = [
            "## Skill 索引",
            "处理特定格式文件前，必须先 read_file 对应 skill 的 SKILL.md，再 execute_python 按说明调用 scripts。",
            "browser 任务：先 read_file skills/browser/SKILL.md（auth→login_method→work），再按需读 "
            "WRITE_VERIFY.md / AUTH_PRIORITY.md / LOGIN_METHOD.md 等；restore 后 need_login 必须先 login_method 面板；"
            "写操作后必须 verify（见 WRITE_VERIFY.md）；勿使用 session_bridge（未接入）。",
            "Skill 文件位于 workspace 相对路径 `skills/{name}/`：",
        ]
        for entry in entries:
            lines.append(f"- **{entry.name}**: {entry.description}")
        return "\n".join(lines)
