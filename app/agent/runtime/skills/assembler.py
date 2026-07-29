"""组装 Canvas 系统 Skill Index 与 always_load 正文"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from app.agent.runtime.skills.index_preamble import CANVAS_SKILL_INDEX_PREAMBLE
from app.agent.runtime.skills.library import CanvasSkillLibrary
from app.agent.runtime.skills.models import SkillDefinition
from app.agent.runtime.skills.prompt_format import (
    format_selected_skill_bodies_text,
    format_user_skill_index_text,
)
from app.agent.runtime.skills.registry import CanvasSkillRegistry
from app.contracts.turn_content import TurnReferenceIndex
from app.server.ports.product import SelectedSkillDTO

TURN_REFERENCES_SKILL_NAME = "canvas_turn_references"


def _reference_index_nonempty(reference_index: TurnReferenceIndex) -> bool:
    """判断本回合是否有 Turn References"""
    inline = reference_index.inline
    materials = reference_index.materials
    return bool(inline.assets or inline.nodes or materials.assets or materials.nodes)


def _with_turn_references_inlined(
    skills: list[SkillDefinition],
    *,
    reference_index: TurnReferenceIndex,
) -> list[SkillDefinition]:
    """有 Turn References 时 copy turn-ref skill 并临时 always_load；不改 registry 缓存"""
    if not _reference_index_nonempty(reference_index):
        return list(skills)
    return [
        replace(skill, always_load=True)
        if skill.name == TURN_REFERENCES_SKILL_NAME and not skill.always_load
        else skill
        for skill in skills
    ]


def build_turn_skill_library(*, reference_index: TurnReferenceIndex) -> CanvasSkillLibrary:
    """为本回合构建可读系统 skill 库"""
    return CanvasSkillLibrary(
        _with_turn_references_inlined(
            CanvasSkillRegistry.load(),
            reference_index=reference_index,
        )
    )


def assemble_canvas_skills_block(
    *,
    skill_library: CanvasSkillLibrary,
    user_index_items: Sequence[SelectedSkillDTO],
    selected_skills: Sequence[SelectedSkillDTO],
) -> str:
    """组装系统 Index → preamble → always_load → 用户 skill"""
    parts: list[str] = ["# Skills", "", "## Skill Index"]
    parts.extend(skill_library.index_lines())
    parts.append("")
    parts.append(CANVAS_SKILL_INDEX_PREAMBLE.strip())

    always_load = sorted(
        (skill for skill in skill_library.definitions() if skill.always_load),
        key=lambda s: (s.priority, s.name),
    )
    for skill in always_load:
        parts.append(f"## {skill.name}\n\n{skill.body}")

    selected_paths = {item.path for item in selected_skills}
    user_index = format_user_skill_index_text(user_index_items, selected_paths)
    if user_index:
        parts.append(user_index)
    bodies = format_selected_skill_bodies_text(selected_skills)
    if bodies:
        parts.append(bodies)

    return "\n\n".join(parts)
