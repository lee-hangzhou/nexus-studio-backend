from enum import StrEnum


class SkillSurface(StrEnum):
    """技能挂载面"""

    CHAT = "chat"
    CANVAS = "canvas"
    PROMPT_ASSISTANT = "prompt_assistant"


class SkillScope(StrEnum):
    """技能作用域"""

    USER = "user"
    PROJECT = "project"
