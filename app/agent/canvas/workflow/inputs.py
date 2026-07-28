from __future__ import annotations

from app.agent.canvas.node_submit.collect_refs import pick_connected_reference_asset_ids
from app.agent.canvas.node_submit.labels import assign_media_label, node_kind_to_media_kind
from app.agent.runtime.ports import get_canvas_port
from app.server.canvas.domain.enums import (
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.canvas.domain.models import (
    CanvasInputSource,
    CanvasInputWait,
    CanvasInputWaitReason,
    RefSlot,
    ResolvedCanvasInputs,
    UpstreamText,
)
from app.server.canvas.domain.node_data import (
    data_output_asset_ids,
    data_output_text,
    data_prompt_text,
    data_status,
    parse_node_data,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.ports.product import CanvasEdgeDTO, CanvasNodeDTO


def _asset_ids(node: CanvasNodeDTO) -> list[int]:
    return list(data_output_asset_ids(parse_node_data(node.data)))


def _node_to_graph(node: CanvasNodeDTO) -> dict:
    data = parse_node_data(node.data)
    return {
        "id": node.id,
        "data": {
            "status": data_status(data).value,
            "output_text": data_output_text(data),
            "output_asset_ids": _asset_ids(node),
            "prompt": data.prompt,
            "content": data.content,
            "prompt_content": (
                [seg.model_dump(mode="json") for seg in data.prompt_content]
                if data.prompt_content
                else None
            ),
        },
    }


def _edge_to_graph(edge: CanvasEdgeDTO) -> dict:
    return {
        "source": edge.source_node_id,
        "target": edge.target_node_id,
        "data": {"source_port": edge.source_port.value, "target_port": edge.target_port.value},
    }


async def resolve_node_inputs(episode_id: int, node_id: str) -> ResolvedCanvasInputs:
    """沿依赖边解析结构化事实：local_prompt、upstream_texts、refs、waiting_on"""
    graph = await get_canvas_port().get_incoming_graph(episode_id, node_id)
    nodes_by_id = {node.id: node for node in graph.nodes}
    target = nodes_by_id.get(node_id)
    if target is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")

    target_data = parse_node_data(target.data)
    local_prompt = data_prompt_text(target_data, target.kind).strip()
    waiting_on: list[CanvasInputWait] = []
    sources: list[CanvasInputSource] = []
    graph_nodes: list[dict] = [_node_to_graph(target)]
    graph_edges: list[dict] = []
    source_rows: dict[str, CanvasNodeDTO] = {}

    for edge in graph.edges:
        graph_edges.append(_edge_to_graph(edge))
        source = nodes_by_id.get(edge.source_node_id)
        if source is None:
            waiting_on.append(
                CanvasInputWait(
                    reason=CanvasInputWaitReason.SOURCE_MISSING,
                    source_node_id=edge.source_node_id,
                )
            )
            continue

        source_rows[source.id] = source
        source_data = parse_node_data(source.data)
        source_status = data_status(source_data)
        if all(str(node["id"]) != source.id for node in graph_nodes):
            graph_nodes.append(_node_to_graph(source))

        source_info = CanvasInputSource(
            node_id=source.id,
            kind=source.kind,
            status=source_status,
            source_port=edge.source_port,
            target_port=edge.target_port,
        )
        sources.append(source_info)

        if source_status == CanvasNodeStatus.FAILED:
            waiting_on.append(
                CanvasInputWait(
                    reason=CanvasInputWaitReason.SOURCE_FAILED,
                    source_node_id=source.id,
                    source=source_info,
                )
            )
            continue

        if edge.target_port == CanvasTargetPort.PROMPT_INPUT:
            if (
                edge.source_port != CanvasSourcePort.OUTPUT_TEXT
                or not data_output_text(source_data).strip()
            ):
                waiting_on.append(
                    CanvasInputWait(
                        reason=CanvasInputWaitReason.TEXT_NOT_READY,
                        source_node_id=source.id,
                        source=source_info,
                    )
                )

        if edge.target_port == CanvasTargetPort.REFERENCE_ASSET:
            ids = _asset_ids(source)
            if (
                edge.source_port != CanvasSourcePort.OUTPUT_ASSET
                or source_status != CanvasNodeStatus.SUCCESS
                or not ids
            ):
                waiting_on.append(
                    CanvasInputWait(
                        reason=CanvasInputWaitReason.ASSET_NOT_READY,
                        source_node_id=source.id,
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
    source_rows: dict[str, CanvasNodeDTO],
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
            return source_id, source.kind
    return "", CanvasNodeKind.IMAGE
