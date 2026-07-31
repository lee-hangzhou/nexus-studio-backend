from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class RoomTimelineMessage:
    """群聊时间线条目（仅用户可见对话，不含工具轨迹）"""

    role: str
    content: str
    speaker_name: str | None = None


def build_room_timeline_block(
    messages: Sequence[RoomTimelineMessage],
    *,
    max_messages: int = 40,
) -> str:
    """把群聊已发生对话压成 turn 上下文字符串；空则返回空串"""
    if not messages:
        return ""
    clipped = list(messages)[-max_messages:]
    lines: list[str] = ["## 本群已发生对话", "以下是项目群里用户、项目助手与在场专家的可见发言，按时间顺序："]
    for item in clipped:
        content = (item.content or "").strip()
        if not content:
            continue
        if item.role == "user":
            speaker = "用户"
        elif item.speaker_name and item.speaker_name.strip():
            speaker = item.speaker_name.strip()
        elif item.role == "assistant":
            speaker = "项目助手"
        else:
            continue
        # 单行摘要，避免把过长工具噪声灌进上下文
        summary = " ".join(content.split())
        if len(summary) > 500:
            summary = summary[:500] + "…"
        lines.append(f"- {speaker}：{summary}")
    if len(lines) <= 2:
        return ""
    lines.append("回答关于「刚才聊了什么」的问题时，必须依据上述群对话，不得假装不知情")
    return "\n".join(lines)
