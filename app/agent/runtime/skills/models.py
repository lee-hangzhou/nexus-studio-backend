from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillDefinition:
    """Canvas 系统 skill 定义"""

    name: str
    description: str
    priority: int
    always_load: bool
    body: str
