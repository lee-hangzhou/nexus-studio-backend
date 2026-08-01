"""工坊可邀请专家目录的确定性 prompt 片段"""

from __future__ import annotations


def format_invite_directory_prompt() -> str:
    """返回本回合权威 invite 目录：`preset_key=名称` 列表"""
    from app.server.workshop.domain.presets import list_invite_directory

    parts = [f"{item.key}={item.name}" for item in list_invite_directory()]
    if not parts:
        raise RuntimeError("workshop invite directory is empty")
    return "可邀请专家（preset_key=名称）：" + "；".join(parts)
