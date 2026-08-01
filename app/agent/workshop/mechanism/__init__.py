"""工坊机制 skill 加载与组装"""

from __future__ import annotations

from app.agent.workshop.mechanism.assembler import (
    WorkshopMechanismSurface,
    assemble_workshop_mechanism_skills_block,
)
from app.agent.workshop.mechanism.invite_directory import format_invite_directory_prompt
from app.agent.workshop.mechanism.registry import WorkshopMechanismSkillRegistry

__all__ = [
    "WorkshopMechanismSkillRegistry",
    "WorkshopMechanismSurface",
    "assemble_workshop_mechanism_skills_block",
    "format_invite_directory_prompt",
]
