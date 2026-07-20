from __future__ import annotations

from app.canvas.node_submit.types import (
    ManualMaterialRef,
    MentionItemRef,
    SubmitMaterialRefs,
    WorkflowPromptContent,
)

_MEDIA_SEGMENT_TYPES = frozenset({"image_url", "video_url", "audio_url"})


def _push_unique_id(target: list[int], seen: set[int], value: int | None) -> None:
    if value is None or value <= 0 or value in seen:
        return
    seen.add(value)
    target.append(value)


def pick_connected_reference_asset_ids(
    node_id: str,
    nodes: list[dict],
    edges: list[dict],
) -> list[int]:
    """移植 pickConnectedReferenceAssetIds：edges 数组顺序，REFERENCE_ASSET + SUCCESS。"""
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
    ids: list[int] = []
    seen: set[int] = set()
    for seg in content:
        if seg.get("type") not in _MEDIA_SEGMENT_TYPES:
            continue
        asset_id = seg.get("assetId")
        if not isinstance(asset_id, int) or asset_id <= 0 or asset_id in seen:
            continue
        seen.add(asset_id)
        ids.append(asset_id)
    return ids


def collect_asset_ids_from_mention_items(items: list[MentionItemRef]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        if item.type == "text":
            continue
        _push_unique_id(ids, seen, item.asset_id)
    return ids


def collect_submit_material_refs(
    *,
    content: WorkflowPromptContent,
    connected_asset_ids: list[int],
    manual_refs: list[ManualMaterialRef] | None = None,
    preview_media_refs: list[MentionItemRef] | None = None,
) -> SubmitMaterialRefs:
    """移植 collectSubmitMaterialRefs 四段合并与去重顺序。"""
    ref_asset_ids: list[int] = []
    ref_attachment_ids: list[int] = []
    seen_assets: set[int] = set()
    seen_attachments: set[int] = set()

    for asset_id in connected_asset_ids:
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for asset_id in collect_asset_ids_from_content(content):
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for asset_id in collect_asset_ids_from_mention_items(preview_media_refs or []):
        _push_unique_id(ref_asset_ids, seen_assets, asset_id)

    for ref in manual_refs or []:
        _push_unique_id(ref_asset_ids, seen_assets, ref.asset_id)
        _push_unique_id(ref_attachment_ids, seen_attachments, ref.material_id)

    return SubmitMaterialRefs(
        ref_asset_ids=tuple(ref_asset_ids),
        ref_attachment_ids=tuple(ref_attachment_ids),
    )
