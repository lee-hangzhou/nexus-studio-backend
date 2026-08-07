from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent.canvas.node_submit.collect_refs import (
    collect_library_ref_asset_ids,
    collect_submit_material_refs,
)
from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.canvas.node_submit.types import ManualMaterialRef
from app.contracts.canvas import ImagePromptLibraryRef
from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.ports.product import CanvasEdgeDTO, CanvasGraphDTO, CanvasNodeDTO


def test_collect_library_ref_asset_ids_skips_malformed() -> None:
    assert collect_library_ref_asset_ids(
        [
            {"asset_id": 12, "type": "image", "url": "https://x/12"},
            {"asset_id": 12},  # 去重
            {"asset_id": 0},  # 非法 id
            {"url": "no-id"},  # 缺 asset_id
            "not-a-dict",  # 非法条目
            {"asset_id": 7},
        ]
    ) == [12, 7]
    assert collect_library_ref_asset_ids(None) == []
    assert collect_library_ref_asset_ids([]) == []


def test_collect_library_ref_asset_ids_accepts_typed_refs() -> None:
    typed = ImagePromptLibraryRef(
        asset_id=9,
        type="image",
        url="https://x/9",
        name="snap.png",
    )
    assert collect_library_ref_asset_ids([typed, {"asset_id": 12}]) == [9, 12]


def test_collect_submit_material_refs_appends_library_refs_last() -> None:
    result = collect_submit_material_refs(
        content=[],
        connected_asset_ids=[11],
        manual_refs=[ManualMaterialRef(asset_id=13)],
        preview_media_refs=None,
        library_ref_ids=[12],
    )
    assert result.ref_asset_ids == (11, 13, 12)


def test_collect_submit_material_refs_library_dedup() -> None:
    result = collect_submit_material_refs(
        content=[],
        connected_asset_ids=[11],
        manual_refs=None,
        preview_media_refs=None,
        library_ref_ids=[11, 12, 12],
    )
    assert result.ref_asset_ids == (11, 12)


def _target_node(*, library_refs: list[dict] | None = None) -> CanvasNodeDTO:
    data: dict = {"prompt": "猫", "title": "target"}
    if library_refs is not None:
        data["library_refs"] = library_refs
    return CanvasNodeDTO(
        id="target-node",
        episode_id=1,
        kind=CanvasNodeKind.IMAGE,
        revision=1,
        position_x=0.0,
        position_y=0.0,
        width=None,
        height=None,
        data=data,
    )


def _source_node(asset_ids: list[int]) -> CanvasNodeDTO:
    return CanvasNodeDTO(
        id="source-node",
        episode_id=1,
        kind=CanvasNodeKind.IMAGE,
        revision=1,
        position_x=10.0,
        position_y=10.0,
        width=None,
        height=None,
        data={"prompt": "源图", "status": "success", "output_asset_ids": asset_ids},
    )


def _ref_edge() -> CanvasEdgeDTO:
    return CanvasEdgeDTO(
        id="edge-1",
        revision=1,
        source_node_id="source-node",
        target_node_id="target-node",
        source_port=CanvasSourcePort.OUTPUT_ASSET,
        target_port=CanvasTargetPort.REFERENCE_ASSET,
        edge_type=CanvasEdgeType.DEPENDENCY,
        metadata={},
    )


def _mock_port(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: CanvasNodeDTO,
    source: CanvasNodeDTO,
    edge: CanvasEdgeDTO,
) -> None:
    port = AsyncMock()
    port.get_incoming_graph = AsyncMock(return_value=CanvasGraphDTO(nodes=(target, source), edges=(edge,)))
    monkeypatch.setattr(
        "app.agent.canvas.node_submit.prepare.get_canvas_port",
        lambda: port,
    )
    monkeypatch.setattr(
        "app.agent.canvas.workflow.inputs.get_canvas_port",
        lambda: port,
    )


@pytest.mark.asyncio
async def test_prepare_manual_requires_self_library_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标节点自带 library_refs 时, 手动提交必须带上才通过严格校验"""
    target = _target_node(library_refs=[{"asset_id": 12, "type": "image", "url": "https://x/12"}])
    _mock_port(monkeypatch, target=target, source=_source_node([11]), edge=_ref_edge())

    result = await prepare_node_submit(
        1,
        "target-node",
        mode="manual",
        prompt="生成",
        ref_asset_ids=[11, 12],
    )
    assert result.prompt == "生成"
    assert result.ref_asset_ids == (11, 12)


@pytest.mark.asyncio
async def test_prepare_manual_rejects_missing_self_library_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """手动提交漏掉自身 library_refs 时必须 fail-closed"""
    target = _target_node(library_refs=[{"asset_id": 12, "type": "image", "url": "https://x/12"}])
    _mock_port(monkeypatch, target=target, source=_source_node([11]), edge=_ref_edge())

    with pytest.raises(AppError) as exc_info:
        await prepare_node_submit(
            1,
            "target-node",
            mode="manual",
            prompt="生成",
            ref_asset_ids=[11],
        )

    assert exc_info.value.code == ErrorCode.CANVAS_SUBMIT_REF_MISMATCH


@pytest.mark.asyncio
async def test_prepare_manual_orders_library_refs_after_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """library_refs 排序在 connected 之后, 与前端收集顺序一致"""
    target = _target_node(library_refs=[{"asset_id": 12, "type": "image", "url": "https://x/12"}])
    _mock_port(monkeypatch, target=target, source=_source_node([11]), edge=_ref_edge())

    with pytest.raises(AppError):
        await prepare_node_submit(
            1,
            "target-node",
            mode="manual",
            prompt="生成",
            ref_asset_ids=[12, 11],
        )


@pytest.mark.asyncio
async def test_prepare_agent_auto_merges_library_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 提交可省略自身 library_refs, 后端自动并入最终 refs"""
    target = _target_node(library_refs=[{"asset_id": 12, "type": "image", "url": "https://x/12"}])
    _mock_port(monkeypatch, target=target, source=_source_node([11]), edge=_ref_edge())

    result = await prepare_node_submit(
        1,
        "target-node",
        mode="agent",
        prompt="参考源图生成",
        ref_asset_ids=[11],
    )
    assert result.ref_asset_ids == (11, 12)


@pytest.mark.asyncio
async def test_prepare_agent_empty_refs_includes_library_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _target_node(library_refs=[{"asset_id": 12, "type": "image", "url": "https://x/12"}])
    _mock_port(monkeypatch, target=target, source=_source_node([11]), edge=_ref_edge())

    result = await prepare_node_submit(
        1,
        "target-node",
        mode="agent",
        prompt="生成",
        ref_asset_ids=None,
    )
    assert result.ref_asset_ids == (11, 12)
