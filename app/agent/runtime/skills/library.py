"""当前轮次可读的 canvas 系统 skill 库"""

from __future__ import annotations

from app.agent.runtime.skills.models import SkillDefinition


class CanvasSkillLibrary:
    """按 skill name 索引；供 read_canvas_skill 读取正文"""

    def __init__(self, skills: list[SkillDefinition]) -> None:
        by_name: dict[str, SkillDefinition] = {}
        for skill in skills:
            if skill.name in by_name:
                raise RuntimeError(f"duplicate canvas skill name: {skill.name}")
            by_name[skill.name] = skill
        self._by_name = by_name

    def definitions(self) -> list[SkillDefinition]:
        """返回本回合全部系统 skill 定义"""
        return list(self._by_name.values())

    def index_lines(self) -> list[str]:
        """渲染系统 Skill Index 行"""
        lines: list[str] = []
        for name in sorted(self._by_name):
            skill = self._by_name[name]
            lines.append(f"- name={skill.name}: {skill.description}")
        return lines

    def read(self, name: str) -> str:
        """读取系统 skill 正文；未知 name 抛 KeyError"""
        skill = self._by_name.get(name)
        if skill is None:
            raise KeyError(name)
        return skill.body
