from __future__ import annotations

from langchain_core.tools import StructuredTool

from app.agent.runtime.memory.tool_factory import build_canvas_memory_tools as _build
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard


def build_canvas_memory_tools(
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """构建画布用户与项目记忆工具"""
    return _build(loop_guard=loop_guard)
