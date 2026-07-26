from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasPatchOperation,
    CanvasSourcePort,
    CanvasTargetPort,
)


class CanvasContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class CanvasPosition(CanvasContract):
    x: float
    y: float


class CreateNodePayload(CanvasContract):
    kind: CanvasNodeKind
    position: CanvasPosition
    title: str = ""
    input_prompt: str = ""
    output_text: str = ""
    status: CanvasNodeStatus = CanvasNodeStatus.IDLE
    model_id: str | None = None
    voice_id: str | None = None
    ratio: str | None = None
    duration_sec: int | None = None
    resolution: str | None = None
    task_id: int | None = None
    output_asset_ids: list[int] | None = None
    error_message: str | None = None


class UpdateNodePatch(CanvasContract):
    position: CanvasPosition | None = None
    title: str | None = None
    input_prompt: str | None = None
    output_text: str | None = None
    status: CanvasNodeStatus | None = None
    model_id: str | None = None
    voice_id: str | None = None
    ratio: str | None = None
    duration_sec: int | None = None
    resolution: str | None = None
    task_id: int | None = None
    output_asset_ids: list[int] | None = None
    error_message: str | None = None
    kind: CanvasNodeKind | None = None


class CreateEdgePayload(CanvasContract):
    source: UUID
    target: UUID
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType = CanvasEdgeType.DEPENDENCY
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateNodeConnectAnchor(CanvasContract):
    """拉线新建节点时，与锚点节点在同一事务内连边。"""

    node_id: UUID
    side: Literal["left", "right"]
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType = CanvasEdgeType.DEPENDENCY
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateNodeOp(CanvasContract):
    op: Literal[CanvasPatchOperation.CREATE_NODE]
    node: CreateNodePayload
    connect_anchor: CreateNodeConnectAnchor | None = None


class UpdateNodeOp(CanvasContract):
    op: Literal[CanvasPatchOperation.UPDATE_NODE]
    node_id: UUID
    patch: UpdateNodePatch


class DeleteNodeOp(CanvasContract):
    op: Literal[CanvasPatchOperation.DELETE_NODE]
    node_id: UUID


class ConnectNodesOp(CanvasContract):
    op: Literal[CanvasPatchOperation.CONNECT]
    edge: CreateEdgePayload


class DisconnectNodesOp(CanvasContract):
    op: Literal[CanvasPatchOperation.DISCONNECT]
    edge_id: UUID


CanvasPatchOp = Annotated[
    CreateNodeOp | UpdateNodeOp | DeleteNodeOp | ConnectNodesOp | DisconnectNodesOp,
    Field(discriminator="op"),
]


class CanvasNodeView(CanvasContract):
    id: UUID
    kind: CanvasNodeKind
    position: CanvasPosition
    title: str = ""
    input_prompt: str = ""
    output_text: str = ""
    status: CanvasNodeStatus = CanvasNodeStatus.IDLE
    model_id: str | None = None
    voice_id: str | None = None
    ratio: str | None = None
    duration_sec: int | None = None
    resolution: str | None = None
    task_id: int | None = None
    output_asset_ids: list[int] | None = None
    output_asset_urls: list[str] | None = None
    error_message: str | None = None


class CanvasEdgeView(CanvasContract):
    id: UUID
    source: UUID
    target: UUID
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanvasSnapshot(CanvasContract):
    project_id: int
    episode_id: int
    revision: int
    nodes: list[CanvasNodeView]
    edges: list[CanvasEdgeView]


class CanvasPatchRequest(CanvasContract):
    expected_revision: int
    ops: list[CanvasPatchOp] = Field(min_length=1)


class CanvasPatchResponse(CanvasContract):
    revision: int
    op_id: UUID | None = None
    nodes: list[CanvasNodeView] = Field(default_factory=list)
    edges: list[CanvasEdgeView] = Field(default_factory=list)
    deleted_node_ids: list[UUID] = Field(default_factory=list)
    deleted_edge_ids: list[UUID] = Field(default_factory=list)


class GenerationProgress(CanvasContract):
    node_id: UUID
    task_id: int | None = None
    status: CanvasNodeStatus
    revision: int
