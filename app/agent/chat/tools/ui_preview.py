from __future__ import annotations

import json

from app.agent.chat.tools.result import ToolResult, ToolResultProtocolError
from app.agent.runtime.tools.user_skill_protocol import WRITE_USER_SKILL_FILE

MEMORY_RECALL_TOOLS = frozenset(
    {
        "recall_user_memory",
        "recall_project_memory",
    }
)
MEMORY_LIST_TOOLS = frozenset()
MEMORY_MANAGE_TOOLS = frozenset({"manage_user_memory", "manage_project_memory"})
MEMORY_TOOL_NAMES = MEMORY_RECALL_TOOLS | MEMORY_MANAGE_TOOLS
BROWSER_TOOL_NAMES = frozenset(
    {
        "browser_exec_script",
        "browser_capture_state",
        "signal_browser_blocked",
        "request_user_gate",
    }
)
CANVAS_TOOL_NAMES = frozenset(
    {
        "query_canvas_nodes",
        "apply_canvas_patch",
        "list_generate_models",
        "submit_node_generation",
        "list_node_generations",
    }
)

_STATUS_PHRASES = frozenset(
    {
        "执行中…",
        "参数异常，正在自动修复",
        "参数已自动修复",
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


def _parse_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _canvas_preview(tool_name: str, output: str, *, ok: bool) -> str:
    if not ok:
        return "画布操作未完成"
    data = _parse_json_object(output)
    if tool_name == "query_canvas_nodes":
        if data is not None:
            matched = data.get("matched")
            if isinstance(matched, int):
                return f"已读取 {matched} 个节点"
            nodes = data.get("nodes")
            if isinstance(nodes, list):
                return f"已读取 {len(nodes)} 个节点"
        return "已读取画布"
    if tool_name == "apply_canvas_patch":
        return "已更新画布"
    if tool_name == "list_generate_models":
        if data is not None:
            models = data.get("models")
            if isinstance(models, list):
                return f"已查询 {len(models)} 个可用模型"
        return "已查询可用模型"
    if tool_name == "submit_node_generation":
        return "已提交生成任务"
    if tool_name == "list_node_generations":
        return "已查询生成状态"
    return "已完成"


def _looks_like_payload(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("{") or stripped.startswith("[")


def sanitize_tool_step_preview(tool_name: str, preview: str, *, ok: bool | None = None) -> str:
    """用户可见 tool 步骤摘要：不暴露工具原始 JSON / 内部字段。

    `preview` 为 ToolFinished 载荷，即 ToolResult 信封字符串；也可传入已截断的展示文本。
    """
    try:
        parsed = ToolResult.parse_tool_message(preview)
        text = parsed.display_text
        resolved_ok = parsed.success if ok is None else ok
    except ToolResultProtocolError:
        text = preview
        resolved_ok = ok
        parsed = None

    if text in _STATUS_PHRASES:
        return text
    if text.startswith("失败:"):
        return text[:200]

    if tool_name == WRITE_USER_SKILL_FILE:
        return "已写入用户 Skill" if resolved_ok is not False else "技能写入未完成"

    if tool_name in BROWSER_TOOL_NAMES:
        if resolved_ok is False:
            if tool_name == "request_user_gate" and "gate_setup_failed" in text:
                return "交互面板不可用"
            return "页面操作未完成"
        if tool_name == "browser_capture_state":
            return "已记录排查快照"
        if tool_name == "signal_browser_blocked":
            return "页面需要验证"
        if tool_name == "request_user_gate":
            return "正在准备操作面板"
        return "已检查页面"

    if tool_name in CANVAS_TOOL_NAMES:
        output = parsed.output if parsed is not None else text
        return _canvas_preview(tool_name, output, ok=resolved_ok is not False)

    if tool_name in MEMORY_TOOL_NAMES:
        if parsed is None or not parsed.success:
            return "记忆操作未完成"
        count = _count_memory_items(parsed.output)
        if tool_name in MEMORY_RECALL_TOOLS:
            return f"已找到 {count} 条相关记忆" if count > 0 else "未找到相关记忆"
        if tool_name in MEMORY_LIST_TOOLS:
            return f"共 {count} 条已存记忆" if count > 0 else "暂无已存记忆"
        if tool_name in MEMORY_MANAGE_TOOLS:
            return "已更新长期记忆"
        return "已完成"

    # 其余工具：只给状态句，不把 output/JSON 推到前端。
    if resolved_ok is False:
        return "执行未完成"
    if tool_name == "publish_file":
        # Chat 侧用 attachment_id=… 解析交付物；保留短机器可读摘要。
        return text[:200] if "attachment_id=" in text else "已发布文件"
    if _looks_like_payload(text):
        return "已完成"
    return "已完成"
