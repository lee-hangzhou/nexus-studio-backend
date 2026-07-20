from __future__ import annotations

from uuid import UUID

from app.canvas.node_submit.collect_refs import pick_connected_reference_asset_ids
from app.canvas.node_submit.labels import assign_media_label, node_kind_to_media_kind
from app.domain.canvas.enums import (
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.domain.canvas.models import (
    CanvasInputSource,
    CanvasInputWait,
    CanvasInputWaitReason,
    RefSlot,
    ResolvedCanvasInputs,
    UpstreamText,
)
from app.models.canvas_edges import CanvasEdges
from app.models.canvas_nodes import CanvasNodes


def _asset_ids(row: CanvasNodes) -> list[int]:
    """从节点行解析 output_asset_ids 整数列表"""
    raw = row.output_asset_ids
    return [int(item) for item in raw] if isinstance(raw, list) else []


def _node_row_to_graph(row: CanvasNodes) -> dict:
    return {
        "id": str(row.id),
        "data": {
            "status": row.status,
            "output_text": row.output_text,
            "output_asset_ids": _asset_ids(row),
        },
    }


def _edge_row_to_graph(edge: CanvasEdges) -> dict:
    return {
        "source": str(edge.source_node_id),
        "target": str(edge.target_node_id),
        "data": {"source_port": edge.source_port, "target_port": edge.target_port},
    }


async def resolve_node_inputs(project_id: int, node_id: str) -> ResolvedCanvasInputs:
    """沿依赖边解析结构化事实：local_prompt、upstream_texts、refs、waiting_on。"""
    target = await CanvasNodes.get(id=UUID(node_id), project_id=project_id, deleted_at__isnull=True)
    local_prompt = target.input_prompt.strip()
    waiting_on: list[CanvasInputWait] = []
    sources: list[CanvasInputSource] = []

    edges = await CanvasEdges.filter(
        project_id=project_id,
        target_node_id=UUID(node_id),
        deleted_at__isnull=True,
    ).all()
    graph_nodes: list[dict] = [_node_row_to_graph(target)]
    graph_edges: list[dict] = []
    source_rows: dict[str, CanvasNodes] = {}

    for edge in edges:
        graph_edges.append(_edge_row_to_graph(edge))
        source = await CanvasNodes.get_or_none(
            id=edge.source_node_id,
            project_id=project_id,
            deleted_at__isnull=True,
        )
        if source is None:
            waiting_on.append(
                CanvasInputWait(
                    reason=CanvasInputWaitReason.SOURCE_MISSING,
                    source_node_id=str(edge.source_node_id),
                )
            )
            continue

        source_rows[str(source.id)] = source
        if all(str(node["id"]) != str(source.id) for node in graph_nodes):
            graph_nodes.append(_node_row_to_graph(source))

        source_info = CanvasInputSource(
            node_id=str(source.id),
            kind=CanvasNodeKind(source.kind),
            status=CanvasNodeStatus(source.status),
            source_port=CanvasSourcePort(edge.source_port),
            target_port=CanvasTargetPort(edge.target_port),
        )
        sources.append(source_info)

        if source.status == CanvasNodeStatus.FAILED:
            waiting_on.append(
                CanvasInputWait(
                    reason=CanvasInputWaitReason.SOURCE_FAILED,
                    source_node_id=str(source.id),
                    source=source_info,
                )
            )
            continue

        if edge.target_port == CanvasTargetPort.PROMPT_INPUT:
            if edge.source_port != CanvasSourcePort.OUTPUT_TEXT or not source.output_text.strip():
                waiting_on.append(
                    CanvasInputWait(
                        reason=CanvasInputWaitReason.TEXT_NOT_READY,
                        source_node_id=str(source.id),
                        source=source_info,
                    )
                )

        if edge.target_port == CanvasTargetPort.REFERENCE_ASSET:
            ids = _asset_ids(source)
            if (
                edge.source_port != CanvasSourcePort.OUTPUT_ASSET
                or source.status != CanvasNodeStatus.SUCCESS
                or not ids
            ):
                waiting_on.append(
                    CanvasInputWait(
                        reason=CanvasInputWaitReason.ASSET_NOT_READY,
                        source_node_id=str(source.id),
                        source=source_info,
                    )
                )

    upstream_texts = _collect_upstream_texts(node_id, graph_nodes, graph_edges)

    connected_asset_ids = pick_connected_reference_asset_ids(node_id, graph_nodes, graph_edges)
    refs: list[RefSlot] = []
    media_slot = 0
    for asset_id in connected_asset_ids:
        source_node_id, source_kind = _find_ref_source(graph_edges, source_rows, node_id, asset_id)
        media_kind = node_kind_to_media_kind(source_kind)
        media_slot += 1
        refs.append(
            RefSlot(
                slot=media_slot,
                label=assign_media_label(media_kind, media_slot),
                asset_id=asset_id,
                source_node_id=source_node_id,
                kind=source_kind,
            )
        )

    return ResolvedCanvasInputs(
        node_id=node_id,
        local_prompt=local_prompt,
        upstream_texts=tuple(upstream_texts),
        refs=tuple(refs),
        waiting_on=tuple(waiting_on),
        sources=tuple(sources),
    )


def _collect_upstream_texts(
    node_id: str,
    graph_nodes: list[dict],
    graph_edges: list[dict],
) -> list[UpstreamText]:
    node_by_id = {str(node["id"]): node for node in graph_nodes}
    texts: list[UpstreamText] = []
    seen: set[str] = set()
    for edge in graph_edges:
        if str(edge.get("target")) != node_id:
            continue
        data = edge.get("data") or {}
        if data.get("target_port") != "prompt_input" or data.get("source_port") != "output_text":
            continue
        source_id = str(edge.get("source"))
        source = node_by_id.get(source_id)
        text = str((source or {}).get("data", {}).get("output_text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(UpstreamText(text=text, source_node_id=source_id))
    return texts


def _find_ref_source(
    edges: list[dict],
    source_rows: dict[str, CanvasNodes],
    node_id: str,
    asset_id: int,
) -> tuple[str, CanvasNodeKind]:
    for edge in edges:
        if str(edge.get("target")) != node_id:
            continue
        data = edge.get("data") or {}
        if data.get("target_port") != "reference_asset" or data.get("source_port") != "output_asset":
            continue
        source_id = str(edge.get("source"))
        source = source_rows.get(source_id)
        if source is None:
            continue
        if asset_id in _asset_ids(source):
            return source_id, CanvasNodeKind(source.kind)
    return "", CanvasNodeKind.IMAGE
