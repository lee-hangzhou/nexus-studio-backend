from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.canvas.workflow.inputs import resolve_node_inputs
from app.agent.chat.tools.result import ToolResult


class ResolveNodeInputsInput(BaseModel):
    """resolve_node_inputs 工具入参"""

    node_id: str = Field(description="需要解析输入依赖的画布节点 UUID")


def build_resolve_node_inputs_tool(episode_id: int) -> StructuredTool:
    """构建 resolve_node_inputs 结构化工具"""
    async def _run(node_id: str) -> str:
        """解析节点依赖边, 返回 local_prompt、upstream_texts、refs、waiting_on"""
        resolved = await resolve_node_inputs(episode_id, node_id)
        payload = {
            **asdict(resolved),
            "ready": resolved.ready,
        }
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="resolve_node_inputs",
        description=(
            "Resolve a canvas node's structured inputs from dependency edges. "
            "Use this before submitting generation for a node that consumes upstream node outputs. "
            "Returns local_prompt, upstream_texts, refs (slot/label/asset_id), sources, and waiting_on. "
            "Merge upstream text into one prompt yourself; pass ref_asset_ids in refs order to submit_node_generation."
        ),
        args_schema=ResolveNodeInputsInput,
    )
