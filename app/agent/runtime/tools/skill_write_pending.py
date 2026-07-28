from __future__ import annotations

from typing import Any

from app.agent.runtime.tools.user_skill_protocol import (
    SKILL_WRITE_OPERATION_TYPE,
    WRITE_USER_SKILL_FILE,
)
from app.contracts.canvas import PendingSkillWriteOperation
from app.server.skills.domain.enums import SkillScope


def parse_skill_revision(raw: Any) -> int | None:
    """解析可选 CAS revision；非法值抛 ValueError"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("invalid skill write revision")
    if isinstance(raw, int):
        if raw < 1:
            raise ValueError("invalid skill write revision")
        return raw
    if isinstance(raw, float) and raw.is_integer():
        value = int(raw)
        if value < 1:
            raise ValueError("invalid skill write revision")
        return value
    if isinstance(raw, str):
        text = raw.strip()
        if text.isdigit():
            value = int(text)
            if value < 1:
                raise ValueError("invalid skill write revision")
            return value
    raise ValueError("invalid skill write revision")


def build_skill_write_operation(name: str, args: dict[str, Any] | None, *, surface: str) -> dict[str, Any] | None:
    """从 write_user_skill_file 待确认参数构建 operation 载荷

    description: 缺省/None → None（覆盖保留原描述）；显式空串保留为 ""（触发补全）
    revision 非法时仍发出 operation，并标 revision_invalid，由确认路径 reject
    """
    if name != WRITE_USER_SKILL_FILE or not isinstance(args, dict):
        return None
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    revision_invalid = False
    revision: int | None
    try:
        revision = parse_skill_revision(args.get("revision"))
    except ValueError:
        revision = None
        revision_invalid = True
    if "description" not in args:
        description: str | None = None
    else:
        raw_desc = args.get("description")
        description = None if raw_desc is None else str(raw_desc)
    name_raw = args.get("name")
    content_raw = args.get("content")
    pending = PendingSkillWriteOperation(
        type=SKILL_WRITE_OPERATION_TYPE,
        path=path.strip(),
        scope=str(SkillScope.USER),
        surface=surface if surface in {"chat", "canvas"} else "canvas",
        name="" if name_raw is None else str(name_raw),
        description=description,
        content="" if content_raw is None else str(content_raw),
        revision=revision,
        revision_invalid=revision_invalid,
    )
    return pending.model_dump(mode="json")
