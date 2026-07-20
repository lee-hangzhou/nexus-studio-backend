from __future__ import annotations

import json
from enum import StrEnum
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.canvas.services.canvas_service import node_view_from_row, refresh_node_asset_urls
from app.chat.tools.result import ToolResult
from app.domain.canvas.enums import CanvasNodeKind, CanvasNodeStatus
from app.models.canvas_edges import CanvasEdges
from app.models.canvas_nodes import CanvasNodes
from app.models.canvas_project_meta import CanvasProjectMeta


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


async def _query_canvas_nodes(project_id: int, args: QueryCanvasNodesInput) -> ToolResult:
    """读取画布 revision, 节点, 可选边, 按 detail 控制字段量"""
    meta = await CanvasProjectMeta.filter(project_id=project_id).first()
    revision = int(meta.revision) if meta else 0
    q = CanvasNodes.filter(project_id=project_id, deleted_at__isnull=True)
    if args.node_ids:
        uuids = []
        for nid in args.node_ids:
            try:
                uuids.append(UUID(nid))
            except ValueError:
                return ToolResult.fail("invalid_node_id", detail=nid)
        q = q.filter(id__in=uuids)
    if args.kind:
        q = q.filter(kind=args.kind)
    if args.status:
        q = q.filter(status=args.status)
    rows = await q.all()
    node_views = [node_view_from_row(row) for row in rows]
    if args.detail == CanvasQueryDetail.FULL:
        await refresh_node_asset_urls(node_views)
    node_views_by_id = {str(view.id): view for view in node_views}
    status_counts: dict[str, int] = {}
    nodes_out: list[dict] = []
    for row in rows:
        # standard 只含模型决策必需字段, full 含资产与错误信息
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
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
            item["output_asset_ids"] = row.output_asset_ids
            item["output_asset_urls"] = node_views_by_id[str(row.id)].output_asset_urls
            item["error_message"] = row.error_message
        nodes_out.append(item)
    edges_out: list[dict[str, str]] = []
    if args.include_edges:
        # 依赖边供 Agent 判断 text, image, video 链路顺序
        edges = await CanvasEdges.filter(project_id=project_id, deleted_at__isnull=True).all()
        edges_out = [
            {
                "id": str(e.id),
                "source": str(e.source_node_id),
                "target": str(e.target_node_id),
                "source_port": e.source_port,
                "target_port": e.target_port,
                "edge_type": e.edge_type,
                "metadata": e.metadata or {},
            }
            for e in edges
        ]
    payload = {
        "revision": revision,
        "matched": len(nodes_out),
        "status_counts": status_counts,
        "nodes": nodes_out,
        "edges": edges_out if args.include_edges else [],
    }
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 8000:
        text = text[:8000] + "\n\n[已截断]"
    return ToolResult.ok(text)


def build_query_canvas_nodes_tool(project_id: int) -> StructuredTool:
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
        return (await _query_canvas_nodes(project_id, args)).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="query_canvas_nodes",
        description=(
            "Query canvas nodes and optional edges for this project. "
            "Call before patch or generation when you need current layout. "
            "Returns revision for apply_canvas_patch."
        ),
        args_schema=QueryCanvasNodesInput,
    )
