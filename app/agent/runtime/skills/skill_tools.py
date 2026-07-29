from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.skills.library import CanvasSkillLibrary


class ReadCanvasSkillInput(BaseModel):
    """read_canvas_skill 入参"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Skill name from the Canvas Skill Index")


def build_read_canvas_skill_tool(library: CanvasSkillLibrary) -> StructuredTool:
    """构建按需读取 canvas 系统 skill 正文的工具"""

    async def read_canvas_skill(name: str) -> str:
        """按 name 返回系统 skill 正文"""
        try:
            body = library.read(name)
        except KeyError:
            return ToolResult.fail(
                "skill_not_found",
                detail=f"unknown canvas skill name={name}",
            ).to_tool_message()
        return ToolResult.ok(body).to_tool_message()

    return StructuredTool.from_function(
        coroutine=read_canvas_skill,
        name="read_canvas_skill",
        description=(
            "Read a canvas system skill body by name from the Skill Index "
            "(e.g. name=canvas_operations or name=canvas_generation). "
            "Do not pass user skill paths. "
            "Call before write/generation tools when the tool description requires it."
        ),
        args_schema=ReadCanvasSkillInput,
    )
