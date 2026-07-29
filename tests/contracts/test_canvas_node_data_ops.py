from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.canvas.tools.canvas_write import (
    ApplyCanvasPatchInput,
    ApplyCanvasEdgeOperationInput,
    llm_node_operation_to_patch_op,
)
from app.agent.canvas.turn.pending import (
    edited_tool_args_from_pending_operation,
    enrich_pending_operation,
)
from app.contracts.canvas import CanvasNodeData, CreateNodePayload


def test_create_node_payload_rejects_projection_fields() -> None:
    with pytest.raises(ValidationError):
        CreateNodePayload(
            kind="image",
            position={"x": 1, "y": 2},
            data=CanvasNodeData(prompt="hi", generate_task_id=12),
        )
    with pytest.raises(ValidationError):
        CreateNodePayload(
            kind="image",
            position={"x": 1, "y": 2},
            data=CanvasNodeData(prompt="hi", status="success"),
        )


def test_apply_canvas_patch_input_is_single_operation() -> None:
    args = ApplyCanvasPatchInput(
        operation={
            "op": "create_node",
            "node": {
                "kind": "text",
                "position": {"x": 0, "y": 0},
                "data": {"content": "hello", "title": "t"},
            },
        }
    )
    op = llm_node_operation_to_patch_op(args.operation)
    assert op.op == "create_node"
    assert op.node.data.content == "hello"


def test_apply_canvas_patch_input_accepts_model_instance() -> None:
    """StructuredTool args_schema 校验后会把 BaseModel 实例直接传给工具，不得再拒"""
    from app.agent.canvas.tools.patch_llm_schemas import PatchCreateNodeOpLlm

    model = PatchCreateNodeOpLlm.model_validate(
        {
            "op": "create_node",
            "node": {
                "kind": "image",
                "position": {"x": 1, "y": 2},
                "data": {"prompt": "cat", "title": "t"},
            },
        }
    )
    args = ApplyCanvasPatchInput(operation=model)
    op = llm_node_operation_to_patch_op(args.operation)
    assert op.op == "create_node"
    assert op.node.data.prompt == "cat"


def test_edge_operation_connect() -> None:
    args = ApplyCanvasEdgeOperationInput(
        operation={
            "op": "connect",
            "edge": {
                "source": "11111111-1111-1111-1111-111111111111",
                "target": "22222222-2222-2222-2222-222222222222",
                "source_port": "output_text",
                "target_port": "prompt_input",
            },
        }
    )
    assert args.operation.op == "connect"


@pytest.mark.asyncio
async def test_enrich_and_resume_create_pending() -> None:
    operation, status = await enrich_pending_operation(
        "apply_canvas_patch",
        {
            "operation": {
                "op": "create_node",
                "node": {
                    "kind": "video",
                    "position": {"x": 10, "y": 20},
                    "data": {"prompt": "dance", "title": "v"},
                },
            }
        },
        surface="canvas",
    )
    assert status == "ok"
    assert operation is not None
    assert operation["type"] == "create"
    assert len(operation["nodes"]) == 1
    # enrich dump 默认带齐 CanvasNodeData 键（含 null 投影）；原样回写会炸契约
    assert "status" in operation["nodes"][0]["data"]
    with pytest.raises(ValidationError):
        CreateNodePayload.model_validate(
            {
                "kind": operation["nodes"][0]["kind"],
                "position": operation["nodes"][0]["position"],
                "data": operation["nodes"][0]["data"],
            }
        )
    edited = edited_tool_args_from_pending_operation("apply_canvas_patch", operation)
    assert edited["operation"]["op"] == "create_node"
    assert edited["operation"]["node"]["data"]["prompt"] == "dance"
    for key in ("status", "generate_task_id", "generate_error", "output_asset_ids"):
        assert key not in edited["operation"]["node"]["data"]
    CreateNodePayload.model_validate(edited["operation"]["node"])


@pytest.mark.asyncio
async def test_update_pending_writeback_strips_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update enrich 合并存量投影字段后，确认回写仍须剥成可写子集"""
    from unittest.mock import AsyncMock
    from app.server.ports.product import CanvasNodeDTO
    from app.server.canvas.domain.enums import CanvasNodeKind
    from app.contracts.canvas import NODE_DATA_CLIENT_FORBIDDEN_KEYS, UpdateNodePayload

    node_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    existing = CanvasNodeDTO(
        id=node_id,
        episode_id=1,
        kind=CanvasNodeKind.IMAGE,
        revision=3,
        position_x=1.0,
        position_y=2.0,
        width=None,
        height=None,
        data={
            "title": "old",
            "prompt": "old-prompt",
            "status": "success",
            "generate_task_id": 99,
            "generate_error": None,
            "output_asset_ids": [1],
        },
    )
    port = AsyncMock()
    port.get_node = AsyncMock(return_value=existing)
    monkeypatch.setattr(
        "app.agent.canvas.turn.pending.get_canvas_port",
        lambda: port,
    )

    operation, status = await enrich_pending_operation(
        "apply_canvas_patch",
        {
            "operation": {
                "op": "update_node",
                "node": {
                    "id": node_id,
                    "revision": 3,
                    "data": {"prompt": "new-prompt"},
                },
            }
        },
        surface="canvas",
        episode_id=1,
    )
    assert status == "ok"
    assert operation is not None
    # 展示视图可含投影
    view_data = operation["nodes"][0]["data"]
    assert view_data.get("status") == "success"
    assert view_data.get("generate_task_id") == 99
    assert view_data.get("prompt") == "new-prompt"

    edited = edited_tool_args_from_pending_operation("apply_canvas_patch", operation)
    data = edited["operation"]["node"]["data"]
    assert data["prompt"] == "new-prompt"
    for key in NODE_DATA_CLIENT_FORBIDDEN_KEYS:
        assert key not in data
    UpdateNodePayload.model_validate(edited["operation"]["node"])


@pytest.mark.asyncio
async def test_enrich_generate_pending_preserves_refs() -> None:
    operation, status = await enrich_pending_operation(
        "submit_node_generation",
        {
            "node_id": "33333333-3333-3333-3333-333333333333",
            "kind": "image",
            "prompt": "a cat",
            "model_id": "nano-banana",
            "ratio": "16:9",
            "ref_asset_ids": [11, 22],
            "count": 2,
        },
        surface="canvas",
    )
    assert status == "ok"
    assert operation is not None
    assert operation["type"] == "generate"
    assert operation["submit_args"]["ref_asset_ids"] == [11, 22]
    args = edited_tool_args_from_pending_operation(
        "submit_node_generation",
        operation,
    )
    assert args["prompt"] == "a cat"
    assert args["model_id"] == "nano-banana"
    assert args["kind"] == "image"
    assert args["ref_asset_ids"] == [11, 22]
    assert args["count"] == 2


@pytest.mark.asyncio
async def test_enrich_generate_invalid_kind_fails() -> None:
    operation, status = await enrich_pending_operation(
        "submit_node_generation",
        {
            "node_id": "33333333-3333-3333-3333-333333333333",
            "kind": "not-a-kind",
            "prompt": "x",
            "model_id": "m",
        },
        surface="canvas",
    )
    assert status == "failed"
    assert operation is None


def test_apply_canvas_patch_rejects_ops_batch_and_delete() -> None:
    with pytest.raises(ValidationError):
        ApplyCanvasPatchInput(ops=[{"op": "create_node", "node": {"kind": "text", "position": {"x": 0, "y": 0}, "data": {}}}])
    with pytest.raises(ValidationError):
        ApplyCanvasPatchInput(
            operation={
                "op": "delete_node",
                "node_id": "33333333-3333-3333-3333-333333333333",
                "expected_revision": 1,
            }
        )


def test_text_content_must_be_string_media_segments() -> None:
    with pytest.raises(ValidationError):
        CreateNodePayload(
            kind="text",
            position={"x": 0, "y": 0},
            data=CanvasNodeData(content=[{"type": "text", "text": "x"}]),
        )
    payload = CreateNodePayload(
        kind="image",
        position={"x": 0, "y": 0},
        data=CanvasNodeData(prompt="p", content=[{"type": "text", "text": "x"}]),
    )
    assert isinstance(payload.data.content, list)


def test_manual_confirm_tools_exclude_edge_tool() -> None:
    from app.server.infra.config import settings

    tools = settings.canvas_manual_confirm_tools
    assert "apply_canvas_patch" in tools
    assert "submit_node_generation" in tools
    assert "apply_canvas_edge_operation" not in tools
    assert "apply_canvas_arrange" not in tools


def test_dump_client_writable_strips_projection_and_nulls() -> None:
    from app.server.canvas.domain.node_data import dump_client_writable_node_data

    raw = CanvasNodeData(
        prompt="p",
        title="t",
        status="success",
        generate_task_id=7,
        generate_error=None,
    )
    dumped = dump_client_writable_node_data(raw)
    assert dumped == {"prompt": "p", "title": "t"}
    CreateNodePayload(kind="image", position={"x": 0, "y": 0}, data=CanvasNodeData.model_validate(dumped))


def test_patch_node_data_llm_preserves_constraints_and_forbids_projection() -> None:
    from annotated_types import Ge
    from pydantic import ValidationError as PydValidationError

    from app.agent.canvas.tools.patch_llm_schemas import (
        PatchNodeDataLlm,
        _clone_optional_field,
    )
    from app.contracts.canvas import NODE_DATA_CLIENT_FORBIDDEN_KEYS

    for key in NODE_DATA_CLIENT_FORBIDDEN_KEYS:
        assert key not in PatchNodeDataLlm.model_fields
    with pytest.raises(PydValidationError):
        PatchNodeDataLlm.model_validate({"generate_task_id": 1})
    assert (
        PatchNodeDataLlm.model_fields["prompt"].annotation
        == CanvasNodeData.model_fields["prompt"].annotation
    )
    # 派生克隆须保留源 Field 约束（以带 ge 的投影字段为样例）
    cloned = _clone_optional_field(CanvasNodeData.model_fields["generate_task_id"])
    assert any(isinstance(item, Ge) and item.ge == 1 for item in cloned.metadata)
