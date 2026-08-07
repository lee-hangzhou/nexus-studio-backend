from __future__ import annotations

from app.server.canvas.domain.node_data import (
    data_output_asset_ids,
    data_output_text,
    data_prompt_text,
    data_status,
    parse_node_data,
)
from app.server.ports.product import CanvasEdgeDTO, CanvasNodeDTO


def graph_node_data(node: CanvasNodeDTO) -> dict:
    """画布节点序列化为图事实 dict（submit 校验与 inputs 解析共用同一视图）"""
    data = parse_node_data(node.data)
    return {
        "id": node.id,
        "data": {
            "status": data_status(data).value,
            "input_prompt": data_prompt_text(data, node.kind),
            "output_text": data_output_text(data),
            "output_asset_ids": list(data_output_asset_ids(data)),
            "prompt": data.prompt,
            "content": data.content,
            "prompt_content": (
                [seg.model_dump(mode="json") for seg in data.prompt_content]
                if data.prompt_content
                else None
            ),
            "library_refs": (
                [ref.model_dump(mode="json") for ref in data.library_refs]
                if data.library_refs
                else None
            ),
        },
    }


def graph_edge_data(edge: CanvasEdgeDTO) -> dict:
    return {
        "source": edge.source_node_id,
        "target": edge.target_node_id,
        "data": {"source_port": edge.source_port.value, "target_port": edge.target_port.value},
    }
