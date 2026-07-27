from __future__ import annotations

from collections.abc import Sequence

from app.server.ports.product import SelectedSkillDTO


def format_user_skill_index_text(
    index_items: Sequence[SelectedSkillDTO],
    selected_paths: set[str],
) -> str:
    """格式化用户技能索引段落"""
    if not index_items:
        return ""
    lines = ["## 用户 Skill 索引"]
    for item in index_items:
        marker = " [required if selected]" if item.path in selected_paths else ""
        lines.append(f"- {item.path} ({item.scope}): {item.description}{marker}")
    return "\n".join(lines)


def format_selected_skill_bodies_text(selected_items: Sequence[SelectedSkillDTO]) -> str:
    """格式化必选用户技能正文段落"""
    if not selected_items:
        return ""
    parts = ["## 必选用户 Skill"]
    for item in selected_items:
        parts.append(f"### {item.path}\n{item.content}")
    return "\n\n".join(parts)
