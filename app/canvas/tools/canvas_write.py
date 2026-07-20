from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.canvas.errors import REVISION_CONFLICT
from app.canvas.schemas.api import CanvasPatchOp
from app.canvas.services.canvas_service import CanvasRevisionConflictError, canvas_service
from app.chat.tools.result import ToolResult


class ApplyCanvasPatchInput(BaseModel):
    """apply_canvas_patch 工具入参 schema"""

    ops: list[CanvasPatchOp] = Field(
        min_length=1,
        description="画布补丁操作列表，例如 create_node、update_node、connect、disconnect",
    )
    expected_revision: int = Field(description="来自 query_canvas_nodes 的当前 revision，用于并发冲突保护")


async def _apply_patch(
    project_id: int,
    args: ApplyCanvasPatchInput,
    *,
    user_id: int | None,
    turn_id: str | None,
) -> ToolResult:
    """校验 patch 并写入画布, 返回 JSON 结构化结果"""
    try:
        result = await canvas_service.apply_patch(
            project_id,
            args.ops,
            args.expected_revision,
            user_id=user_id,
            turn_id=turn_id,
        )
        return ToolResult.ok(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        )
    except CanvasRevisionConflictError as exc:
        # revision 冲突时 Agent 应先重新 query_canvas_nodes
        return ToolResult.fail(REVISION_CONFLICT, detail=str(exc.details))


def build_apply_canvas_patch_tool(
    project_id: int,
    *,
    user_id: int,
    turn_id_holder: dict[str, str | None],
) -> StructuredTool:
    """构建 apply_canvas_patch 结构化工具"""
    async def _run(ops: list[dict], expected_revision: int) -> str:
        """工具入口, 校验参数后调用 _apply_patch"""
        args = ApplyCanvasPatchInput(ops=ops, expected_revision=expected_revision)
        return (
            await _apply_patch(
                project_id,
                args,
                user_id=user_id,
                turn_id=turn_id_holder.get("turn_id"),
            )
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="apply_canvas_patch",
        description=(
            "Apply canvas patch ops. Required: expected_revision, ops[]. "
            "create_node: {\"op\":\"create_node\",\"node\":{\"kind\":\"video\",\"position\":{\"x\":100,\"y\":100},"
            "\"input_prompt\":\"...\"}}. "
            "connect: edge.source and edge.target must be existing nodes[].id from a prior "
            "query_canvas_nodes or apply_canvas_patch result — never invented ids. "
            "Do not connect in the same patch as create_node; create first, read returned ids, "
            "then connect in a follow-up patch. "
            "disconnect: {\"op\":\"disconnect\",\"edge_id\":\"<from edges[].id>\"}."
        ),
        args_schema=ApplyCanvasPatchInput,
    )
