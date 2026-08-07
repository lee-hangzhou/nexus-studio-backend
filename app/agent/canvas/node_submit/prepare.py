from __future__ import annotations

from typing import Literal

from app.agent.canvas.node_submit.collect_refs import (
    collect_library_ref_asset_ids,
    collect_submit_material_refs,
    pick_connected_reference_asset_ids,
)
from app.agent.canvas.node_submit.graph_serializer import graph_edge_data, graph_node_data
from app.agent.canvas.node_submit.types import (
    ManualMaterialRef,
    MentionItemRef,
    PrepareNodeSubmitResult,
    WorkflowPromptContent,
)
from app.agent.canvas.workflow.inputs import resolve_node_inputs
from app.agent.runtime.ports import get_canvas_port
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


async def _load_incoming_graph(episode_id: int, node_id: str) -> tuple[list[dict], list[dict]]:
    graph = await get_canvas_port().get_incoming_graph(episode_id, node_id)
    return [graph_node_data(node) for node in graph.nodes], [graph_edge_data(edge) for edge in graph.edges]


def _self_library_ref_asset_ids(node_id: str, nodes: list[dict]) -> list[int]:
    """收集目标节点自身 data.library_refs 的 asset_id（本地上传素材标签）"""
    for node in nodes:
        if str(node.get("id")) != node_id:
            continue
        raw_refs = (node.get("data") or {}).get("library_refs")
        if not isinstance(raw_refs, list):
            return []
        return collect_library_ref_asset_ids(raw_refs)
    return []


def _expected_refs_from_graph(
    node_id: str,
    nodes: list[dict],
    edges: list[dict],
    *,
    content: WorkflowPromptContent,
    manual_refs: list[ManualMaterialRef] | None,
    preview_media_refs: list[MentionItemRef] | None,
):
    connected_asset_ids = pick_connected_reference_asset_ids(node_id, nodes, edges)
    return collect_submit_material_refs(
        content=content,
        connected_asset_ids=connected_asset_ids,
        manual_refs=manual_refs,
        preview_media_refs=preview_media_refs,
        library_ref_ids=_self_library_ref_asset_ids(node_id, nodes),
    )


def _validate_ref_asset_ids(
    *,
    node_id: str,
    expected_asset_ids: tuple[int, ...],
    got_asset_ids: list[int] | None,
) -> None:
    got_assets = tuple(got_asset_ids or ())
    if got_assets == expected_asset_ids:
        return
    raise AppError(
        ErrorCode.CANVAS_SUBMIT_REF_MISMATCH,
        "提交引用与画布状态不一致，请刷新后重试",
        details={
            "node_id": node_id,
            "expected": {"ref_asset_ids": list(expected_asset_ids)},
            "got": {"ref_asset_ids": list(got_assets)},
        },
    )


def _validate_agent_ref_asset_ids(
    *,
    node_id: str,
    resolved_asset_ids: tuple[int, ...],
    got_asset_ids: list[int] | None,
) -> tuple[int, ...]:
    got = tuple(got_asset_ids or ())
    if not got:
        return resolved_asset_ids
    if got == resolved_asset_ids:
        return got
    # 允许同序子集：必须是 resolved 的前缀子序列（顺序一致）
    cursor = 0
    for asset_id in got:
        while cursor < len(resolved_asset_ids) and resolved_asset_ids[cursor] != asset_id:
            cursor += 1
        if cursor >= len(resolved_asset_ids):
            raise AppError(
                ErrorCode.CANVAS_SUBMIT_REF_MISMATCH,
                "ref_asset_ids 顺序必须与 resolve_node_inputs.refs 一致",
                details={"node_id": node_id, "expected": list(resolved_asset_ids), "got": list(got)},
            )
        cursor += 1
    return got


async def prepare_node_submit(
    episode_id: int,
    node_id: str,
    *,
    mode: Literal["manual", "agent"],
    prompt: str | None = None,
    ref_asset_ids: list[int] | None = None,
    content: WorkflowPromptContent | None = None,
    manual_refs: list[ManualMaterialRef] | None = None,
    preview_media_refs: list[MentionItemRef] | None = None,
) -> PrepareNodeSubmitResult:
    """画布节点提交唯一契约：manual 校验 refs，agent 校验 prompt + 有序 refs。"""
    if mode == "manual":
        nodes, edges = await _load_incoming_graph(episode_id, node_id)
        expected = _expected_refs_from_graph(
            node_id,
            nodes,
            edges,
            content=content or [],
            manual_refs=manual_refs,
            preview_media_refs=preview_media_refs,
        )
        _validate_ref_asset_ids(
            node_id=node_id,
            expected_asset_ids=expected.ref_asset_ids,
            got_asset_ids=ref_asset_ids,
        )
        if not (prompt or "").strip():
            raise AppError(ErrorCode.INVALID_PARAMS, "prompt 不能为空")
        return PrepareNodeSubmitResult(
            prompt=prompt.strip(),
            ref_asset_ids=expected.ref_asset_ids,
        )

    resolved = await resolve_node_inputs(episode_id, node_id)
    if resolved.waiting_on:
        reasons = ", ".join(item.reason.value for item in resolved.waiting_on)
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "节点输入未就绪",
            details={"node_id": node_id, "waiting_on": reasons},
        )
    if not (prompt or "").strip():
        raise AppError(ErrorCode.INVALID_PARAMS, "agent 提交需要非空 prompt")

    resolved_asset_ids = tuple(slot.asset_id for slot in resolved.refs)
    final_asset_ids = _validate_agent_ref_asset_ids(
        node_id=node_id,
        resolved_asset_ids=resolved_asset_ids,
        got_asset_ids=ref_asset_ids,
    )
    final_asset_ids = tuple(dict.fromkeys([*final_asset_ids, *resolved.library_ref_asset_ids]))
    return PrepareNodeSubmitResult(
        prompt=prompt.strip(),
        ref_asset_ids=final_asset_ids,
    )
