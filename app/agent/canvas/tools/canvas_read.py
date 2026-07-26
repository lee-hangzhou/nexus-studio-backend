from __future__ import annotations

import json
from enum import StrEnum
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port
from app.server.canvas.domain.enums import CanvasNodeKind, CanvasNodeStatus


class CanvasQueryDetail(StrEnum):
    """query_canvas_nodes 返回详略级别"""

    SUMMARY = "summary"
    STANDARD = "standard"
    FULL = "full"


class QueryCanvasNodesInput(BaseModel):
    """query_canvas_nodes 工具入参 schema"""

    node_ids: list[str] | None = Field(default=None, description="可选节点 UUID 列表；传入后只返回这些节点")
    kind: CanvasNodeKind | None = None
    status: CanvasNodeStatus | None = None
    detail: CanvasQueryDetail = CanvasQueryDetail.STANDARD
    include_edges: bool = Field(default=False)


async def _query_canvas_nodes(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    args: QueryCanvasNodesInput,
) -> ToolResult:
    """读取画布 revision, 节点, 可选边, 按 detail 控制字段量"""
    node_ids: tuple[str, ...] | None = None
    if args.node_ids:
        for nid in args.node_ids:
            try:
                UUID(nid)
            except ValueError:
                return ToolResult.fail("invalid_node_id", detail=nid)
        node_ids = tuple(args.node_ids)
    graph = await get_canvas_port().get_graph(
        project_id=project_id,
        episode_id=episode_id,
        user_id=user_id,
        node_ids=node_ids,
        kind=args.kind,
        status=args.status,
        include_edges=args.include_edges,
        include_asset_urls=args.detail == CanvasQueryDetail.FULL,
    )
    status_counts: dict[str, int] = {}
    nodes_out: list[dict] = []
    for row in graph.nodes:
        # standard 只含模型决策必需字段, full 含资产与错误信息
        status_counts[row.status.value] = status_counts.get(row.status.value, 0) + 1
        item: dict = {
            "id": str(row.id),
            "kind": row.kind,
            "status": row.status,
            "title": row.title,
        }
        if args.detail in {CanvasQueryDetail.STANDARD, CanvasQueryDetail.FULL}:
            input_prompt = row.input_prompt
            if args.detail == CanvasQueryDetail.STANDARD and len(input_prompt) > 200:
                input_prompt = input_prompt[:200] + "…"
            item.update(
                {
                    "input_prompt": input_prompt,
                    "output_text": row.output_text,
                    "position": {"x": row.position_x, "y": row.position_y},
                    "task_id": row.task_id,
                    "model_id": row.model_id,
                    "ratio": row.ratio,
                    "resolution": row.resolution,
                    "duration_sec": row.duration_sec,
                }
            )
        if args.detail == CanvasQueryDetail.FULL:
            item["output_asset_ids"] = list(row.output_asset_ids)
            item["output_asset_urls"] = list(row.output_asset_urls)
            item["error_message"] = row.error_message
        nodes_out.append(item)
    edges_out: list[dict[str, str]] = []
    if args.include_edges:
        # 依赖边供 Agent 判断 text, image, video 链路顺序
        edges_out = [
            {
                "id": str(e.id),
                "source": e.source_node_id,
                "target": e.target_node_id,
                "source_port": e.source_port.value,
                "target_port": e.target_port.value,
                "edge_type": e.edge_type.value,
                "metadata": e.metadata,
            }
            for e in graph.edges
        ]
    payload = {
        "revision": graph.revision,
        "matched": len(nodes_out),
        "status_counts": status_counts,
        "nodes": nodes_out,
        "edges": edges_out if args.include_edges else [],
    }
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 8000:
        text = text[:8000] + "\n\n[已截断]"
    return ToolResult.ok(text)


def build_query_canvas_nodes_tool(*, project_id: int, episode_id: int, user_id: int) -> StructuredTool:
    """构建 query_canvas_nodes 结构化工具"""
    async def _run(
        node_ids: list[str] | None = None,
        kind: str | None = None,
        status: str | None = None,
        detail: CanvasQueryDetail = CanvasQueryDetail.STANDARD,
        include_edges: bool = False,
    ) -> str:
        """工具入口, 组装 QueryCanvasNodesInput 后查询"""
        args = QueryCanvasNodesInput(
            node_ids=node_ids,
            kind=kind,
            status=status,
            detail=detail,
            include_edges=include_edges,
        )
        return (
            await _query_canvas_nodes(
                project_id=project_id,
                episode_id=episode_id,
                user_id=user_id,
                args=args,
            )
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="query_canvas_nodes",
        description=(
            "Query canvas nodes and optional edges for this episode. "
            "Call before patch or generation when you need current layout. "
            "Returns revision for apply_canvas_patch."
        ),
        args_schema=QueryCanvasNodesInput,
    )
