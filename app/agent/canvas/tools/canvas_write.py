from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.canvas.errors import REVISION_CONFLICT
from app.agent.canvas.tools.patch_llm_schemas import (
    PatchConnectOpLlm,
    PatchCreateNodeOpLlm,
    PatchDisconnectOpLlm,
    PatchEdgeOperationLlm,
    PatchNodeOperationLlm,
    PatchUpdateNodeOpLlm,
)
from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port
from app.contracts.canvas import (
    CanvasNodeData,
    CanvasPatchOp,
    ConnectNodesOp,
    CreateEdgePayload,
    CreateNodeOp,
    CreateNodePayload,
    DisconnectNodesOp,
    UpdateNodeOp,
    UpdateNodePayload,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def _normalize_object_field(value: object, *, field: str) -> object:
    """接受 dict / JSON 字符串 / 已校验的 BaseModel（StructuredTool args_schema 会直接传入模型实例）"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


class ApplyCanvasPatchInput(BaseModel):
    """apply_canvas_patch：单次恰好一个节点操作（create_node | update_node）"""

    model_config = ConfigDict(extra="forbid")

    operation: PatchNodeOperationLlm = Field(
        description=(
            "Exactly one node operation: create_node or update_node. "
            "Do not pass ops[]. Do not delete nodes. "
            "update_node.node.revision must match query_canvas_nodes revision."
        ),
    )

    @field_validator("operation", mode="before")
    @classmethod
    def _normalize_operation(cls, value: object) -> object:
        return _normalize_object_field(value, field="operation")


class ApplyCanvasEdgeOperationInput(BaseModel):
    """apply_canvas_edge_operation：单次恰好一个边操作（connect | disconnect）"""

    model_config = ConfigDict(extra="forbid")

    operation: PatchEdgeOperationLlm = Field(
        description=(
            "Exactly one edge operation: connect or disconnect. "
            "connect requires existing node UUIDs from prior query/create. "
            "disconnect requires edge_id + expected_revision."
        ),
    )

    @field_validator("operation", mode="before")
    @classmethod
    def _normalize_operation(cls, value: object) -> object:
        return _normalize_object_field(value, field="operation")


def llm_node_operation_to_patch_op(raw: PatchNodeOperationLlm) -> CanvasPatchOp:
    # raw 已在 ApplyCanvasPatchInput / Pending* 边界校验，此处信任类型
    if isinstance(raw, PatchCreateNodeOpLlm):
        node = raw.node
        return CreateNodeOp(
            op="create_node",
            node=CreateNodePayload(
                kind=node.kind,
                position=node.position,
                width=node.width,
                height=node.height,
                data=CanvasNodeData.model_validate(
                    node.data.model_dump(mode="python", exclude_unset=True)
                ),
            ),
        )
    if not isinstance(raw, PatchUpdateNodeOpLlm):
        raise TypeError(f"unsupported node operation: {type(raw)!r}")
    node = raw.node
    data = (
        None
        if node.data is None
        else CanvasNodeData.model_validate(node.data.model_dump(mode="python", exclude_unset=True))
    )
    return UpdateNodeOp(
        op="update_node",
        node=UpdateNodePayload(
            id=UUID(str(node.id)),
            revision=node.revision,
            position=node.position,
            width=node.width,
            height=node.height,
            data=data,
        ),
    )


def llm_edge_operation_to_patch_op(raw: PatchEdgeOperationLlm) -> CanvasPatchOp:
    # raw 已在 ApplyCanvasEdgeOperationInput 边界校验，此处信任类型
    if isinstance(raw, PatchConnectOpLlm):
        edge = raw.edge
        return ConnectNodesOp(
            op="connect",
            edge=CreateEdgePayload(
                source=UUID(str(edge.source)),
                target=UUID(str(edge.target)),
                source_port=edge.source_port,
                target_port=edge.target_port,
                edge_type=edge.edge_type,
                metadata=dict(edge.metadata),
            ),
        )
    if not isinstance(raw, PatchDisconnectOpLlm):
        raise TypeError(f"unsupported edge operation: {type(raw)!r}")
    return DisconnectNodesOp(
        op="disconnect",
        edge_id=UUID(str(raw.edge_id)),
        expected_revision=raw.expected_revision,
    )


async def _apply_single_op(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    turn_id: str | None,
    patch_op: CanvasPatchOp,
) -> ToolResult:
    try:
        result = await get_canvas_port().apply_patch(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            ops=[patch_op],
            turn_id=turn_id,
        )
        return ToolResult.ok(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    except AppError as exc:
        if exc.code != int(ErrorCode.CANVAS_REVISION_CONFLICT):
            raise
        return ToolResult.fail(REVISION_CONFLICT, detail=str(exc.details))


def build_apply_canvas_patch_tool(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    turn_id_holder: dict[str, str | None],
) -> StructuredTool:
    """构建单节点 apply_canvas_patch 工具"""

    async def _run(operation: dict) -> str:
        args = ApplyCanvasPatchInput(operation=operation)
        patch_op = llm_node_operation_to_patch_op(args.operation)
        return (
            await _apply_single_op(
                project_id=project_id,
                episode_id=episode_id,
                user_id=user_id,
                turn_id=turn_id_holder.get("turn_id"),
                patch_op=patch_op,
            )
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="apply_canvas_patch",
        description=(
            "REQUIRED this turn: call read_canvas_skill(name=\"canvas_operations\") before this tool. "
            "Listing/querying alone does not waive the read. "
            "Apply exactly one canvas node operation. Required: operation "
            "(create_node | update_node). No ops[], no delete_node, no connect. "
            "update_node.node.revision must match query_canvas_nodes. "
            "In manual mode, confirm-card edits win over the draft tool args. "
            "Do not parallelize with apply_canvas_arrange or submit_node_generation in one reply. "
            "create_node: {\"op\":\"create_node\",\"node\":{\"kind\":\"video\","
            "\"position\":{\"x\":100,\"y\":100},\"data\":{\"title\":\"...\",\"prompt\":\"...\"}}}. "
            "update_node: {\"op\":\"update_node\",\"node\":{\"id\":\"<uuid>\",\"revision\":1,"
            "\"data\":{\"prompt\":\"...\"}}}. "
            "After create, call apply_canvas_edge_operation separately to connect."
        ),
        args_schema=ApplyCanvasPatchInput,
    )


def build_apply_canvas_edge_operation_tool(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    turn_id_holder: dict[str, str | None],
) -> StructuredTool:
    """构建单边 apply_canvas_edge_operation 工具（不进 HITL）"""

    async def _run(operation: dict) -> str:
        args = ApplyCanvasEdgeOperationInput(operation=operation)
        patch_op = llm_edge_operation_to_patch_op(args.operation)
        return (
            await _apply_single_op(
                project_id=project_id,
                episode_id=episode_id,
                user_id=user_id,
                turn_id=turn_id_holder.get("turn_id"),
                patch_op=patch_op,
            )
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="apply_canvas_edge_operation",
        description=(
            "REQUIRED this turn: call read_canvas_skill(name=\"canvas_operations\") before this tool. "
            "Listing/querying alone does not waive the read. "
            "Apply exactly one canvas edge operation. Required: operation "
            "(connect | disconnect). Executes without manual confirm. "
            "connect requires existing node UUIDs from prior query/create. "
            "connect: {\"op\":\"connect\",\"edge\":{\"source\":\"<uuid>\",\"target\":\"<uuid>\","
            "\"source_port\":\"output_text\",\"target_port\":\"prompt_input\","
            "\"edge_type\":\"dependency\"}}. "
            "disconnect: {\"op\":\"disconnect\",\"edge_id\":\"<uuid>\",\"expected_revision\":1}."
        ),
        args_schema=ApplyCanvasEdgeOperationInput,
    )
