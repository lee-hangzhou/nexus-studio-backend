from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.chat.tools.result import TOOL_LOOP_EXHAUSTED, ToolResult
from app.core.turn.tool_loop_guard import TurnToolLoopGuard
from app.services.generation_models import list_generate_models


class ListGenerateModelsInput(BaseModel):
    """list_generate_models 工具入参"""

    kind: str = Field(
        pattern="^(image|video|audio)$",
        description='Exactly one of "image", "video", or "audio" per call.',
    )


def build_list_generate_models_tool(
    loop_guard: TurnToolLoopGuard | None = None,
) -> StructuredTool:
    """构建 list_generate_models 结构化工具"""

    async def _run(kind: str) -> str:
        """按 kind 查询可用生成模型列表"""
        args = {"kind": kind}
        if loop_guard is not None:
            blocked = loop_guard.pre_check("list_generate_models", args=args)
            if blocked is not None:
                return ToolResult.fail(TOOL_LOOP_EXHAUSTED, detail=blocked.detail()).to_tool_message()
        result = await list_generate_models(kind)
        payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        tool_result = ToolResult.ok(json.dumps(payload, ensure_ascii=False))
        if loop_guard is not None:
            loop_guard.record(
                "list_generate_models",
                args=args,
                success=tool_result.success,
                is_empty=False,
            )
        return tool_result.to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="list_generate_models",
        description=(
            "List generation models for one media kind. Args: kind='image', kind='video', or kind='audio'. "
            "Do not pass multiple kinds in one call. "
            "Returns model_id values for submit_node_generation. "
            "Each kind at most once per user turn with substantive success. "
            "A second call with the same kind returns error_type=tool_loop_exhausted (duplicate call guard). "
            "Check prior ToolMessage in this turn before calling."
        ),
        args_schema=ListGenerateModelsInput,
    )
