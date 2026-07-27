"""Chat turn 工具组装：langchain + 技能写入 + MCP，统一受 enable_tools 约束。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from app.agent.chat.mcp.client import load_mcp_tools
from app.agent.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
from app.agent.runtime.tools.user_skill_tools import build_write_user_skill_file_tool
from app.server.skills.domain.enums import SkillSurface


def build_chat_turn_tools(
    ctx: ChatToolContext,
    *,
    enable_tools: bool,
    user_id: int,
) -> list[StructuredTool]:
    """组装本轮 Chat 工具列表；enable_tools=false 时不含技能写入与 MCP"""
    tools = build_langchain_tools(ctx, enable_tools=enable_tools)
    if not enable_tools:
        return tools
    tools.append(
        build_write_user_skill_file_tool(
            surface=SkillSurface.CHAT,
            user_id=user_id,
        )
    )
    tools.extend(load_mcp_tools())
    return tools
