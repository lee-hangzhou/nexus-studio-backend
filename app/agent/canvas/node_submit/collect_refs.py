from __future__ import annotations

from app.agent.canvas.node_submit.types import (
    ManualMaterialRef,
    MentionItemRef,
    SubmitMaterialRefs,
    WorkflowPromptContent,
)

_MEDIA_SEGMENT_TYPES = frozenset({"image_url", "video_url", "audio_url"})


def _push_unique_id(target: list[int], seen: set[int], value: int) -> None:
    """按首次出现顺序写入去重 id"""
    if value <= 0 or value in seen:
        return
    seen.add(value)
    target.append(value)


def pick_connected_reference_asset_ids(
    node_id: str,
    nodes: list[dict],
    edges: list[dict],
) -> list[int]:
    """按 edges 顺序收集 REFERENCE_ASSET 连线的成功节点 output_asset_ids"""
    node_by_id = {str(node["id"]): node for node in nodes}
    ids: list[int] = []
    seen: set[int] = set()

    for edge in edges:
        if str(edge.get("target")) != node_id:
            continue
        data = edge.get("data") or {}
        if data.get("target_port") != "reference_asset" or data.get("source_port") != "output_asset":
            continue
        source = node_by_id.get(str(edge.get("source")))
        if source is None:
            continue
        source_data = source.get("data") or {}
        if source_data.get("status") != "success":
            continue
        raw_ids = source_data.get("output_asset_ids") or []
        if not isinstance(raw_ids, list):
            continue
        for asset_id in raw_ids:
            if not isinstance(asset_id, int) or asset_id <= 0 or asset_id in seen:
                continue
            seen.add(asset_id)
            ids.append(asset_id)

    return ids


def collect_asset_ids_from_content(content: WorkflowPromptContent) -> list[int]:
    """从 WorkflowPromptContent 媒体段收集 asset_id"""
    ids: list[int] = []
    seen: set[int] = set()
    for seg in content:
        if seg.get("type") not in _MEDIA_SEGMENT_TYPES:
            continue
        asset_id = seg.get("asset_id")
        if not isinstance(asset_id, int) or asset_id <= 0 or asset_id in seen:
            continue
        seen.add(asset_id)
        ids.append(asset_id)
    return ids


def collect_asset_ids_from_mention_items(items: list[MentionItemRef]) -> list[int]:
    """从 media mention 列表收集 asset_id; 非法 id fail-closed"""
    ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        if item.asset_id <= 0:
            raise ValueError(f"mention item asset_id must be >= 1, got {item.asset_id}")
        _push_unique_id(ids, seen, item.asset_id)
    return ids


def collect_submit_material_refs(
    *,
    content: WorkflowPromptContent,
    connected_asset_ids: list[int],
    manual_refs: list[ManualMaterialRef] | None = None,
    preview_media_refs: list[MentionItemRef] | None = None,
) -> SubmitMaterialRefs:
    """四段合并去重收集 submit 用 ref_asset_ids"""
    ref_asset_ids: list[int] = []
    seen_assets: set[int] = set()

    for asset_id in connected_asset_ids:
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for asset_id in collect_asset_ids_from_content(content):
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for asset_id in collect_asset_ids_from_mention_items(preview_media_refs or []):
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for ref in manual_refs or []:
        if ref.asset_id <= 0:
            raise ValueError(f"manual_ref asset_id must be >= 1, got {ref.asset_id}")
        _push_unique_id(ref_asset_ids, seen_assets, ref.asset_id)

    return SubmitMaterialRefs(ref_asset_ids=tuple(ref_asset_ids))
