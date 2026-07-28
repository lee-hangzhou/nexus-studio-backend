from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasPatchOperation,
    CanvasPendingOperationType,
    CanvasSessionStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.generation.domain.enums import GenerationKind, ReferenceMode


class CanvasContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class CanvasPosition(CanvasContract):
    x: float
    y: float


class WorkflowPromptTextSegment(CanvasContract):
    type: Literal["text"]
    text: str


class WorkflowPromptMediaSegment(CanvasContract):
    type: Literal["image_url", "video_url", "audio_url"]
    asset_id: int | None = Field(default=None, ge=1)
    url: str
    label: str | None = None
    reference_type: str | None = None


class WorkflowPromptTextRefSegment(CanvasContract):
    type: Literal["text_ref"]
    text: str


WorkflowPromptContentSegment = Annotated[
    WorkflowPromptTextSegment | WorkflowPromptMediaSegment | WorkflowPromptTextRefSegment,
    Field(discriminator="type"),
]


class CanvasNodeConfig(CanvasContract):
    """节点生成配置（对齐参考可写集；不含未接入能力）"""

    model: str | None = None
    text_num: int | None = Field(default=None, ge=1)
    img_num: int | None = Field(default=None, ge=1)
    ratio: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    image_size: str | None = None
    mode: str | None = None
    duration: str | None = None
    duration_sec: int | None = None
    reference_mode: int | None = Field(default=None, ge=1)
    voice_id: str | None = None
    voice_name: str | None = None
    speed: float | None = None
    vol: float | None = None
    pitch: int | None = None
    emotion: str | None = None


class CanvasNodeAssetPath(CanvasContract):
    asset_id: int | None = Field(default=None, ge=1)
    url: str | None = None
    thumb_url: str | None = None


class ImagePromptLibraryRef(CanvasContract):
    asset_id: int = Field(ge=1)
    type: Literal["image", "video", "audio"] | None = None
    url: str
    thumb_url: str | None = None
    name: str | None = None


class CanvasTaskAsset(CanvasContract):
    asset_id: int | None = Field(default=None, ge=1)
    url: str | None = None
    thumb_url: str | None = None


class CanvasTaskResults(CanvasContract):
    status: Literal["success", "failed"]
    assets: list[CanvasTaskAsset] | None = None
    error_message: str | None = None
    raw: Any | None = None


class CanvasNodeData(CanvasContract):
    """节点业务字段；持久化在 canvas_nodes.data JSONB

    语义（对齐参考）：
    - text：content=正文(str)；prompt_content=生成输入(segments)；prompt=派生纯文本
    - media：content=结构化 prompt(segments)；prompt=派生纯文本
    """

    title: str | None = None
    prompt: str | None = None
    content: str | list[WorkflowPromptContentSegment] | None = None
    prompt_content: list[WorkflowPromptContentSegment] | None = None
    config: CanvasNodeConfig | None = None
    model: str | None = None
    status: CanvasNodeStatus | None = None
    generate_task_id: int | None = Field(default=None, ge=1)
    generate_created_at: str | None = None
    generate_operation_type: str | None = None
    generate_error: str | None = None
    asset_id: int | None = Field(default=None, ge=1)
    path: str | None = None
    preview_url: str | None = None
    paths: list[CanvasNodeAssetPath] | None = None
    output_asset_ids: list[int] | None = None
    output_source: Literal["upload", "generated"] | None = None
    library_refs: list[ImagePromptLibraryRef] | None = None
    results: CanvasTaskResults | None = None


# 客户端 patch 禁止写入的投影字段（由 generation / 系统写回）
NODE_DATA_CLIENT_FORBIDDEN_KEYS = frozenset(
    {
        "status",
        "generate_task_id",
        "generate_created_at",
        "generate_operation_type",
        "generate_error",
        "output_asset_ids",
        "asset_id",
        "path",
        "preview_url",
        "paths",
        "results",
        "output_source",
    }
)


class CreateNodePayload(CanvasContract):
    kind: CanvasNodeKind
    position: CanvasPosition
    width: float | None = None
    height: float | None = None
    data: CanvasNodeData = Field(default_factory=CanvasNodeData)

    @model_validator(mode="after")
    def validate_data_by_kind(self) -> CreateNodePayload:
        validate_node_data_content(self.kind, self.data)
        _reject_client_forbidden_data(self.data)
        return self


class UpdateNodePayload(CanvasContract):
    """update_node 载荷；revision 语义等同原 expected_revision"""

    id: UUID
    revision: int = Field(ge=1)
    position: CanvasPosition | None = None
    width: float | None = None
    height: float | None = None
    data: CanvasNodeData | None = None

    @model_validator(mode="after")
    def validate_partial_data(self) -> UpdateNodePayload:
        if self.data is not None:
            _reject_client_forbidden_data(self.data)
        return self


class CreateEdgePayload(CanvasContract):
    source: UUID
    target: UUID
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType = CanvasEdgeType.DEPENDENCY
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateNodeConnectAnchor(CanvasContract):
    """拉线新建节点时，与锚点节点在同一事务内连边"""

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
    node: UpdateNodePayload


class DeleteNodeOp(CanvasContract):
    op: Literal[CanvasPatchOperation.DELETE_NODE]
    node_id: UUID
    expected_revision: int


class ConnectNodesOp(CanvasContract):
    op: Literal[CanvasPatchOperation.CONNECT]
    edge: CreateEdgePayload


class DisconnectNodesOp(CanvasContract):
    op: Literal[CanvasPatchOperation.DISCONNECT]
    edge_id: UUID
    expected_revision: int


CanvasNodeOperation = Annotated[
    CreateNodeOp | UpdateNodeOp,
    Field(discriminator="op"),
]

CanvasEdgeOperation = Annotated[
    ConnectNodesOp | DisconnectNodesOp,
    Field(discriminator="op"),
]

CanvasPatchOp = Annotated[
    CreateNodeOp | UpdateNodeOp | DeleteNodeOp | ConnectNodesOp | DisconnectNodesOp,
    Field(discriminator="op"),
]


class CanvasNodeView(CanvasContract):
    id: UUID
    kind: CanvasNodeKind
    revision: int
    position: CanvasPosition
    width: float | None = None
    height: float | None = None
    data: CanvasNodeData = Field(default_factory=CanvasNodeData)
    output_asset_urls: list[str] | None = None

    @model_validator(mode="after")
    def validate_data_by_kind(self) -> CanvasNodeView:
        validate_node_data_content(self.kind, self.data)
        return self


class CanvasEdgeView(CanvasContract):
    id: UUID
    revision: int
    source: UUID
    target: UUID
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanvasSnapshot(CanvasContract):
    project_id: int
    episode_id: int
    nodes: list[CanvasNodeView]
    edges: list[CanvasEdgeView]


class CanvasPatchRequest(CanvasContract):
    ops: list[CanvasPatchOp] = Field(min_length=1)


class CanvasPatchResponse(CanvasContract):
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


class CanvasRevisionConflictItem(CanvasContract):
    kind: Literal["node", "edge"]
    id: UUID
    expected_revision: int
    actual_revision: int


class CanvasSessionView(CanvasContract):
    """画布 Agent 会话视图"""

    id: int
    episode_id: int
    title: str
    status: CanvasSessionStatus
    is_default: bool
    created_at: str
    updated_at: str


class CanvasSessionCreateRequest(CanvasContract):
    """新建画布会话"""

    title: str | None = None


class CanvasSessionUpdateRequest(CanvasContract):
    """更新画布会话标题"""

    session_id: int
    title: str = Field(min_length=1, max_length=40)


class CanvasSessionIdRequest(CanvasContract):
    """按 session_id 操作"""

    session_id: int


class PendingCanvasPatchOperation(CanvasContract):
    """tool_pending 中展示的 create/update 画布操作"""

    type: Literal[CanvasPendingOperationType.CREATE, CanvasPendingOperationType.UPDATE]
    nodes: list[CanvasNodeView] = Field(default_factory=list)
    edges: list[CanvasEdgeView] = Field(default_factory=list)


class PendingGenerateSubmitArgs(CanvasContract):
    """generate pending 携带的 submit_node_generation 参数快照（结构化，禁止裸 dict）"""

    node_id: str
    kind: GenerationKind
    prompt: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    voice_id: str | None = None
    ratio: str | None = None
    resolution: str | None = None
    count: int = Field(default=1, ge=1, le=6)
    duration: int | None = Field(default=None, ge=3, le=15)
    reference_mode: ReferenceMode | None = None
    ref_asset_ids: list[int] = Field(default_factory=list)
    expected_revision: int | None = None


class PendingGenerateOperation(CanvasContract):
    """tool_pending 中展示的节点生成操作"""

    type: Literal[CanvasPendingOperationType.GENERATE]
    node: CanvasNodeView
    submit_args: PendingGenerateSubmitArgs


class PendingSkillWriteOperation(CanvasContract):
    """tool_pending 中展示的用户 skill 写入操作"""

    type: Literal[CanvasPendingOperationType.SKILL_WRITE]
    path: str = Field(min_length=1)
    scope: Literal["user", "project"] = "user"
    surface: Literal["chat", "canvas"] = "canvas"
    name: str = ""
    description: str | None = None
    content: str = ""
    revision: int | None = None
    revision_invalid: bool | None = None


CanvasToolPendingOperation = Annotated[
    PendingCanvasPatchOperation | PendingGenerateOperation | PendingSkillWriteOperation,
    Field(discriminator="type"),
]


def validate_node_data_content(kind: CanvasNodeKind | str, data: CanvasNodeData) -> None:
    kind_value = kind.value if isinstance(kind, CanvasNodeKind) else str(kind)
    content = data.content
    if kind_value == CanvasNodeKind.TEXT:
        if content is not None and not isinstance(content, str):
            raise ValueError("text node data.content must be string")
        return
    if kind_value in {
        CanvasNodeKind.IMAGE,
        CanvasNodeKind.VIDEO,
        CanvasNodeKind.AUDIO,
    }:
        if content is not None and isinstance(content, str):
            raise ValueError("media node data.content must be prompt content array")
        return


def _reject_client_forbidden_data(data: CanvasNodeData) -> None:
    forbidden = NODE_DATA_CLIENT_FORBIDDEN_KEYS.intersection(data.model_fields_set)
    if forbidden:
        raise ValueError(f"client cannot write node data fields: {sorted(forbidden)}")
