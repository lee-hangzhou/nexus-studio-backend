from __future__ import annotations

import json
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.canvas.errors import REVISION_CONFLICT
from app.agent.canvas.tools.canvas_write import _normalize_object_field
from app.agent.canvas.tools.patch_llm_schemas import PatchCreateEdgePayloadLlm
from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_canvas_port
from app.contracts.canvas import (
    CanvasPatchOp,
    CanvasPosition,
    ConnectNodesOp,
    CreateEdgePayload,
    UpdateNodeOp,
    UpdateNodePayload,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings


def _normalize_object_list(value: object, *, field: str) -> object:
    """接受数组或 JSON 数组字符串；显式 null 失败"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a JSON array") from exc
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [_normalize_object_field(item, field=f"{field}[]") for item in value]


class ArrangeNodeMoveLlm(BaseModel):
    """apply_canvas_arrange 单次位移"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    revision: int = Field(ge=1)
    position: CanvasPosition


class ApplyCanvasArrangeInput(BaseModel):
    """apply_canvas_arrange：批量 position 更新 + 可选连边，一次 apply_patch"""

    model_config = ConfigDict(extra="forbid")

    moves: list[ArrangeNodeMoveLlm] = Field(default_factory=list)
    connections: list[PatchCreateEdgePayloadLlm] = Field(default_factory=list)

    @field_validator("moves", mode="before")
    @classmethod
    def _normalize_moves(cls, value: object) -> object:
        """归一 moves 为对象列表"""
        return _normalize_object_list(value, field="moves")

    @field_validator("connections", mode="before")
    @classmethod
    def _normalize_connections(cls, value: object) -> object:
        """归一 connections 为对象列表"""
        return _normalize_object_list(value, field="connections")

    @model_validator(mode="after")
    def _require_ops(self) -> ApplyCanvasArrangeInput:
        """至少一侧非空；move id 不重复；connection 端点为 UUID"""
        if not self.moves and not self.connections:
            raise ValueError("apply_canvas_arrange requires at least one move or connection")
        move_ids = [item.id for item in self.moves]
        if len(move_ids) != len(set(move_ids)):
            raise ValueError("duplicate move node ids")
        for edge in self.connections:
            UUID(edge.source)
            UUID(edge.target)
        return self


def _build_arrange_ops(
    moves: list[ArrangeNodeMoveLlm],
    connections: list[PatchCreateEdgePayloadLlm],
) -> list[CanvasPatchOp]:
    """将 arrange 入参映射为一次 apply_patch 的 ops"""
    ops: list[CanvasPatchOp] = []
    for move in moves:
        ops.append(
            UpdateNodeOp(
                op="update_node",
                node=UpdateNodePayload(
                    id=move.id,
                    revision=move.revision,
                    position=move.position,
                ),
            )
        )
    for edge in connections:
        ops.append(
            ConnectNodesOp(
                op="connect",
                edge=CreateEdgePayload(
                    source=UUID(edge.source),
                    target=UUID(edge.target),
                    source_port=edge.source_port,
                    target_port=edge.target_port,
                    edge_type=edge.edge_type,
                    metadata=dict(edge.metadata),
                ),
            )
        )
    return ops


def build_apply_canvas_arrange_tool(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    turn_id_holder: dict[str, str | None],
) -> StructuredTool:
    """构建批量布局工具（免 HITL；整批 CAS）"""

    async def _run(
        moves: list[ArrangeNodeMoveLlm],
        connections: list[PatchCreateEdgePayloadLlm],
    ) -> str:
        """执行批量布局；入参已由 args_schema 校验"""
        ops = _build_arrange_ops(moves, connections)
        max_ops = int(settings.CANVAS_ARRANGE_MAX_OPS)
        if len(ops) > max_ops:
            return ToolResult.fail(
                "invalid_arguments",
                detail=f"apply_canvas_arrange exceeds max ops {max_ops}",
            ).to_tool_message()
        try:
            result = await get_canvas_port().apply_patch(
                project_id=project_id,
                episode_id=episode_id,
                user_id=user_id,
                ops=ops,
                turn_id=turn_id_holder.get("turn_id"),
            )
            return ToolResult.ok(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            ).to_tool_message()
        except AppError as exc:
            if exc.code == int(ErrorCode.CANVAS_REVISION_CONFLICT):
                detail = (
                    json.dumps(exc.details, ensure_ascii=False)
                    if exc.details is not None
                    else "revision conflict"
                )
                return ToolResult.fail(REVISION_CONFLICT, detail=detail).to_tool_message()
            raise

    return StructuredTool.from_function(
        coroutine=_run,
        name="apply_canvas_arrange",
        description=(
            "REQUIRED this turn: call read_canvas_skill(name=\"canvas_operations\") before this tool. "
            "Listing/querying alone does not waive the read. "
            "Batch-move node positions and optionally connect edges in one call. "
            "Not for content edits — use apply_canvas_patch for create/update data. "
            "Executes without manual confirm. Whole batch is all-or-nothing CAS; "
            "on revision_conflict re-query and resubmit a full corrected batch. "
            "Do not parallelize with apply_canvas_patch or submit_node_generation in one reply. "
            "Args: moves=[{id, revision, position}], connections=[{source, target, source_port, "
            "target_port, edge_type}] — at least one side non-empty."
        ),
        args_schema=ApplyCanvasArrangeInput,
    )
