"""用户技能 HITL / tool 协议常量（wire 值唯一来源）。"""

from __future__ import annotations

WRITE_USER_SKILL_FILE = "write_user_skill_file"
SKILL_WRITE_OPERATION_TYPE = "skill_write"

# ToolResult.error_type
SKILL_REVISION_CONFLICT = "revision_conflict"
SKILL_NOT_FOUND = "not_found"
