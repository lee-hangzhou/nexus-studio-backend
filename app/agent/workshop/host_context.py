from __future__ import annotations

from typing import Sequence


def build_host_turn_context_block(
    *,
    roster_names: Sequence[str],
    room_member_names: Sequence[str],
    task_titles: Sequence[str],
    invite_directory_names: Sequence[str],
    task_assignee_lines: Sequence[str] = (),
) -> str:
    """组装 Host 回合上下文块，含名册、房间、任务、负责人与可邀请目录"""
    roster_line = "、".join(roster_names) if roster_names else "（暂无）"
    room_line = "、".join(room_member_names) if room_member_names else "（暂无）"
    task_line = "、".join(task_titles) if task_titles else "（暂无）"
    invite_line = "、".join(invite_directory_names) if invite_directory_names else "（暂无）"
    assignee_line = "；".join(task_assignee_lines) if task_assignee_lines else "（暂无）"
    return (
        "## 工坊现状\n"
        f"项目名册专家：{roster_line}\n"
        f"当前房间在场：{room_line}\n"
        f"进行中/待办任务：{task_line}\n"
        f"任务负责人：{assignee_line}\n"
        f"可邀请专家目录：{invite_line}\n"
        "你不是独自工作；回答「还有哪些专家」等问题时必须引用上述名册与目录，"
        "不得声称「就我一个」。"
        "你的发言身份永远是「项目助手」，禁止自称任何在场专家的名字或职责。"
    )
