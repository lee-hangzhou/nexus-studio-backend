from __future__ import annotations

import json
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.canvas.errors import INVALID_NODE_ID
from app.chat.tools.result import ToolResult
from app.models.canvas_nodes import CanvasNodes
from app.services.generation_status import get_generate_task_status


class ListNodeGenerationsInput(BaseModel):
    """list_node_generations 工具入参"""

    node_id: str | None = Field(default=None, description="可选画布节点 UUID；不传则查询项目内最近有任务的节点")
    limit: int = Field(default=20, ge=1, le=50)


async def _list_generations(project_id: int, user_id: int, args: ListNodeGenerationsInput) -> ToolResult:
    """查询单节点或项目内节点关联的 generate_task 状态"""
    task_ids: list[int] = []
    if args.node_id:
        try:
            node = await CanvasNodes.filter(
                id=UUID(args.node_id),
                project_id=project_id,
                deleted_at__isnull=True,
            ).first()
        except ValueError:
            return ToolResult.fail(INVALID_NODE_ID, detail=args.node_id)
        if node is None:
            return ToolResult.fail(INVALID_NODE_ID, detail=args.node_id)
        if node.task_id:
            task_ids.append(int(node.task_id))
    else:
        rows = await CanvasNodes.filter(
            project_id=project_id,
            deleted_at__isnull=True,
            task_id__isnull=False,
        ).limit(args.limit)
        task_ids = [int(r.task_id) for r in rows if r.task_id]

    if not task_ids:
        return ToolResult.ok(json.dumps({"items": []}, ensure_ascii=False))

    items: list[dict] = []
    for task_id in task_ids[: args.limit]:
        try:
            # 复用创作页任务状态服务, 权限与视图一致
            view = await get_generate_task_status(task_id, user_id)
            items.append(view.model_dump(mode="json"))
        except Exception:
            continue
    return ToolResult.ok(json.dumps({"items": items}, ensure_ascii=False))


def build_list_node_generations_tool(project_id: int, user_id: int) -> StructuredTool:
    """构建 list_node_generations 结构化工具"""

    async def _run(node_id: str | None = None, limit: int = 20) -> str:
        """工具入口, 查询节点关联生成任务"""
        args = ListNodeGenerationsInput(node_id=node_id, limit=limit)
        return (await _list_generations(project_id, user_id, args)).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="list_node_generations",
        description="查询画布节点关联的生成任务状态；可指定 node_id，也可查询当前项目最近的节点任务。",
        args_schema=ListNodeGenerationsInput,
    )
