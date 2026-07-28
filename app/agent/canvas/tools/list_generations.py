from __future__ import annotations

import json
from typing import cast

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.canvas.errors import INVALID_NODE_ID
from app.agent.canvas.services.generation_projection import project_from_task
from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port, get_generation_port
from app.server.infra.logger import logger
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


class ListNodeGenerationsInput(BaseModel):
    """list_node_generations 工具入参"""

    node_id: str | None = Field(default=None, description="可选画布节点 UUID；不传则查询项目内最近有任务的节点")
    limit: int = Field(default=20, ge=1, le=50)


async def _list_generations(episode_id: int, user_id: int, args: ListNodeGenerationsInput) -> ToolResult:
    """查询单节点或项目内节点关联的 generate_task 状态"""
    task_ids: list[int] = []
    if args.node_id:
        node = await get_canvas_port().get_node(episode_id, args.node_id)
        if node is None:
            return ToolResult.fail(INVALID_NODE_ID, detail=args.node_id)
        from app.server.canvas.domain.node_data import data_task_id, parse_node_data

        task_id = data_task_id(parse_node_data(node.data))
        if task_id is not None:
            task_ids.append(task_id)
    else:
        task_ids = await get_canvas_port().list_episode_node_task_ids(episode_id, limit=args.limit)

    if not task_ids:
        return ToolResult.ok(json.dumps({"items": []}, ensure_ascii=False))

    items: list[dict] = []
    for task_id in task_ids[: args.limit]:
        try:
            observed = await get_generation_port().observe_task(task_id, user_id)
            view = observed.view
        except AppError as exc:
            if exc.code == int(ErrorCode.TASK_NOT_FOUND):
                continue
            logger.warning(
                "canvas.list_generations.observe_failed",
                task_id=task_id,
                error=str(exc),
            )
            continue
        try:
            await project_from_task(observed.task.id, observed.task.user_id)
        except Exception as exc:
            logger.warning(
                "canvas.list_generations.project_failed",
                task_id=task_id,
                error=str(exc),
            )
        items.append(view.model_dump(mode="json"))
    return ToolResult.ok(json.dumps({"items": items}, ensure_ascii=False))


def build_list_node_generations_tool(episode_id: int, user_id: int) -> StructuredTool:
    """构建 list_node_generations 结构化工具"""

    async def _run(node_id: str | None = None, limit: int = 20) -> str:
        """工具入口, 查询节点关联生成任务"""
        args = ListNodeGenerationsInput(node_id=node_id, limit=limit)
        return cast(str, (await _list_generations(episode_id, user_id, args)).to_tool_message())

    return StructuredTool.from_function(
        coroutine=_run,
        name="list_node_generations",
        description="查询画布节点关联的生成任务状态；可指定 node_id，也可查询当前集最近的节点任务。",
        args_schema=ListNodeGenerationsInput,
    )
