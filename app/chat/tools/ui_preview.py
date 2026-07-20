from __future__ import annotations

import json

from app.chat.tools.result import ToolResult

MEMORY_RECALL_TOOLS = frozenset(
    {
        "recall_user_memory",
        "recall_conversation_memory",
        "recall_project_memory",
    }
)
MEMORY_LIST_TOOLS = frozenset({"list_user_memories", "list_conversation_memories"})
MEMORY_MANAGE_TOOLS = frozenset({"manage_user_memory", "manage_conversation_memory"})
MEMORY_TOOL_NAMES = MEMORY_RECALL_TOOLS | MEMORY_LIST_TOOLS | MEMORY_MANAGE_TOOLS
BROWSER_TOOL_NAMES = frozenset(
    {
        "browser_exec_script",
        "browser_capture_state",
        "signal_browser_blocked",
        "request_user_gate",
    }
)


def _count_memory_items(output: str) -> int:
    text = (output or "").strip()
    if not text or text == "[]":
        return 0
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return 0
        return len(parsed) if isinstance(parsed, list) else 0
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return 0
        return 1 if isinstance(parsed, dict) else 0
    return 0


def sanitize_tool_step_preview(tool_name: str, preview: str, *, ok: bool | None = None) -> str:
    """用户可见 tool_steps 摘要：记忆类工具不暴露 JSON 与设定正文。"""
    if tool_name in BROWSER_TOOL_NAMES:
        if ok is False:
            if tool_name == "request_user_gate" and "gate_setup_failed" in preview:
                return "交互面板不可用"
            return "页面操作未完成"
        if tool_name == "browser_capture_state":
            return "已记录排查快照"
        if tool_name == "signal_browser_blocked":
            return "页面需要验证"
        if tool_name == "request_user_gate":
            return "正在准备操作面板"
        return "已检查页面"

    if tool_name not in MEMORY_TOOL_NAMES:
        return preview[:500]
    if preview in {"执行中…", "参数异常，正在自动修复", "参数已自动修复"}:
        return preview
    if preview.startswith("失败:"):
        return preview[:500]

    parsed = ToolResult.parse_tool_message(preview)
    if not parsed.success:
        return "记忆操作未完成"

    count = _count_memory_items(parsed.output)
    if tool_name in MEMORY_RECALL_TOOLS:
        return f"已找到 {count} 条相关记忆" if count > 0 else "未找到相关记忆"
    if tool_name in MEMORY_LIST_TOOLS:
        return f"共 {count} 条已存记忆" if count > 0 else "暂无已存记忆"
    if tool_name in MEMORY_MANAGE_TOOLS:
        return "已更新长期记忆"
    return "已完成"
