from __future__ import annotations

import json
from enum import StrEnum
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.runtime.tools.result import ToolResult
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


class CanvasNodePositionOut(BaseModel):
    """节点坐标"""

    x: float
    y: float


class CanvasNodeQueryOut(BaseModel):
    """query_canvas_nodes 节点条目；业务字段在 data 中，与写工具同构"""

    id: str
    kind: CanvasNodeKind
    revision: int
    position: CanvasNodePositionOut | None = None
    width: float | None = None
    height: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    output_asset_urls: list[str] | None = None


class CanvasEdgeQueryOut(BaseModel):
    """query_canvas_nodes 边条目"""

    id: str
    revision: int
    source: str
    target: str
    source_port: str
    target_port: str
    edge_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanvasQueryResult(BaseModel):
    """query_canvas_nodes 工具返回体"""

    matched: int
    status_counts: dict[str, int]
    nodes: list[CanvasNodeQueryOut]
    edges: list[CanvasEdgeQueryOut]


def _truncate_data_for_standard(data: dict[str, Any]) -> dict[str, Any]:
    """standard 详略：截断过长文本字段"""
    out = dict(data)
    prompt = out.get("prompt")
    if isinstance(prompt, str) and len(prompt) > 200:
        out["prompt"] = prompt[:200] + "…"
    content = out.get("content")
    if isinstance(content, str) and len(content) > 200:
        out["content"] = content[:200] + "…"
    return out


async def _query_canvas_nodes(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    args: QueryCanvasNodesInput,
) -> ToolResult:
    """读取画布节点与可选边, 按 detail 控制字段量"""
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
    nodes_out: list[CanvasNodeQueryOut] = []
    from app.server.canvas.domain.node_data import data_status, parse_node_data

    for row in graph.nodes:
        data = row.data
        status = data_status(parse_node_data(data)).value
        status_counts[status] = status_counts.get(status, 0) + 1
        if args.detail == CanvasQueryDetail.SUMMARY:
            item = CanvasNodeQueryOut(
                id=str(row.id),
                kind=row.kind,
                revision=row.revision,
                data={
                    "title": data.get("title"),
                    "status": data.get("status") or status,
                },
            )
        elif args.detail == CanvasQueryDetail.STANDARD:
            item = CanvasNodeQueryOut(
                id=str(row.id),
                kind=row.kind,
                revision=row.revision,
                position=CanvasNodePositionOut(x=row.position_x, y=row.position_y),
                width=row.width,
                height=row.height,
                data=_truncate_data_for_standard(data),
            )
        else:
            item = CanvasNodeQueryOut(
                id=str(row.id),
                kind=row.kind,
                revision=row.revision,
                position=CanvasNodePositionOut(x=row.position_x, y=row.position_y),
                width=row.width,
                height=row.height,
                data=data,
                output_asset_urls=list(row.output_asset_urls) or None,
            )
        nodes_out.append(item)
    edges_out: list[CanvasEdgeQueryOut] = []
    if args.include_edges:
        edges_out = [
            CanvasEdgeQueryOut(
                id=e.id,
                revision=e.revision,
                source=e.source_node_id,
                target=e.target_node_id,
                source_port=e.source_port.value,
                target_port=e.target_port.value,
                edge_type=e.edge_type.value,
                metadata=e.metadata,
            )
            for e in graph.edges
        ]
    result = CanvasQueryResult(
        matched=len(nodes_out),
        status_counts=status_counts,
        nodes=nodes_out,
        edges=edges_out,
    )
    return ToolResult.ok(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


def build_query_canvas_nodes_tool(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
) -> StructuredTool:
    """构建 query_canvas_nodes 工具"""

    async def _run(**kwargs: Any) -> str:
        args = QueryCanvasNodesInput.model_validate(kwargs)
        result = await _query_canvas_nodes(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            args=args,
        )
        return result.to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="query_canvas_nodes",
        description=(
            "Query canvas nodes. Returns envelope + structured data blob "
            "(prompt/content/config/status/…). Use node ids from results for patch/connect."
        ),
        args_schema=QueryCanvasNodesInput,
    )
