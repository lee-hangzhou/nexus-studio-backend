"""用户技能工具与 HITL operation 的稳定协议常量。"""

from __future__ import annotations

from app.server.skills.domain.protocol import (
    SKILL_NOT_FOUND,
    SKILL_REVISION_CONFLICT,
    SKILL_WRITE_OPERATION_TYPE,
    WRITE_USER_SKILL_FILE,
)

__all__ = [
    "WRITE_USER_SKILL_FILE",
    "SKILL_WRITE_OPERATION_TYPE",
    "SKILL_REVISION_CONFLICT",
    "SKILL_NOT_FOUND",
]
