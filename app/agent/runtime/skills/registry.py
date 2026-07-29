"""Canvas 系统 skill 注册表，启动时扫描一次"""

from __future__ import annotations

from pathlib import Path

from app.agent.runtime.skills.frontmatter import parse_skill_frontmatter
from app.agent.runtime.skills.models import SkillDefinition

_CANVAS_SKILLS_DIR = Path(__file__).resolve().parents[2] / "canvas" / "skills"


class CanvasSkillRegistry:
    """扫描 `app/agent/canvas/skills/*/SKILL.md` 并缓存"""

    _entries: list[SkillDefinition] | None = None

    @classmethod
    def skills_dir(cls) -> Path:
        """返回 canvas 系统 skill 根目录"""
        return _CANVAS_SKILLS_DIR

    @classmethod
    def load(cls) -> list[SkillDefinition]:
        """加载全部 canvas 系统 skill；解析失败或撞名则启动失败"""
        if cls._entries is not None:
            return cls._entries
        entries: list[SkillDefinition] = []
        seen: set[str] = set()
        root = cls.skills_dir()
        if not root.is_dir():
            raise RuntimeError(f"canvas skills directory missing: {root}")
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                raise RuntimeError(f"canvas skill missing SKILL.md: {child}")
            skill = cls._parse_skill_md(skill_md, expected_name=child.name)
            if skill.name in seen:
                raise RuntimeError(f"duplicate canvas skill name: {skill.name}")
            seen.add(skill.name)
            entries.append(skill)
        if not entries:
            raise RuntimeError(f"no canvas skills found under {root}")
        cls._entries = entries
        return entries

    @classmethod
    def reset(cls) -> None:
        """清空缓存，供测试重载"""
        cls._entries = None

    @classmethod
    def _parse_skill_md(cls, path: Path, *, expected_name: str) -> SkillDefinition:
        """解析单个 SKILL.md 为 SkillDefinition"""
        text = path.read_text(encoding="utf-8")
        meta, body = parse_skill_frontmatter(text)
        name = meta.get("name")
        description = meta.get("description")
        if not name or not description:
            raise RuntimeError(f"Skill frontmatter requires name and description at {path}")
        if name != expected_name:
            raise RuntimeError(
                f"Skill name '{name}' does not match directory '{expected_name}' at {path}"
            )
        if "priority" not in meta:
            raise RuntimeError(f"Skill frontmatter requires priority at {path}")
        if "always_load" not in meta:
            raise RuntimeError(f"Skill frontmatter requires always_load at {path}")
        try:
            priority = int(meta["priority"].strip())
        except ValueError as exc:
            raise RuntimeError(f"invalid priority at {path}") from exc
        always_raw = meta["always_load"].strip().lower()
        if always_raw not in {"true", "false"}:
            raise RuntimeError(f"invalid always_load at {path}")
        if not body:
            raise RuntimeError(f"empty skill body at {path}")
        return SkillDefinition(
            name=name,
            description=description.strip(),
            priority=priority,
            always_load=always_raw == "true",
            body=body,
        )
