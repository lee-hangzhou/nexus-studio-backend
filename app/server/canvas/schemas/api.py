from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from app.contracts.turn_content import TurnContentBlock, TurnUserInput, validate_turn_user_input

from app.server.canvas.schemas.node_execute import NodeExecuteKind
from app.contracts.canvas import (
    CanvasEdgeView,
    CanvasNodeView,
    CanvasPatchOp,
    CanvasPatchRequest,
    CanvasPatchResponse,
    CanvasPosition,
    CanvasSessionCreateRequest,
    CanvasSessionIdRequest,
    CanvasSessionUpdateRequest,
    CanvasSessionView,
    CanvasSnapshot,
    ConnectNodesOp,
    CreateEdgePayload,
    CreateNodeOp,
    CreateNodePayload,
    DeleteNodeOp,
    DisconnectNodesOp,
    GenerationProgress,
    UpdateNodeOp,
    UpdateNodePatch,
)
from app.server.canvas.domain.enums import CanvasNodeStatus


class CanvasTurnRequest(BaseModel):
    """画布 Agent turn 请求体"""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    request_id: UUID4
    content: list[TurnContentBlock] = Field(min_length=1)
    materials: list[Any] = Field(default_factory=list)
    model_key: str | None = None
    client_turn_id: str | None = None
    mode: Literal["auto", "manual"] = "auto"
    enable_tools: bool = True

    @model_validator(mode="after")
    def _validate_turn_input(self) -> CanvasTurnRequest:
        """校验文本必填且本阶段 materials 为空"""
        if self.materials:
            raise ValueError("materials must be empty in this phase")
        try:
            validate_turn_user_input(TurnUserInput(content=self.content, materials=self.materials))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class CanvasResumeRequest(BaseModel):
    """手动模式下恢复被中断工具调用的请求体"""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    request_id: UUID4
    tool_call_id: str
    action: Literal["confirm", "reject"]
    client_turn_id: str = Field(min_length=1)
    model_key: str | None = None
    operation: dict[str, Any] | None = None


class CanvasReconnectRequest(BaseModel):
    """重连一次已存在的可重放流"""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    request_id: UUID4


class CanvasCancelRequest(BaseModel):
    """取消当前 session 的在途 turn"""

    model_config = ConfigDict(extra="forbid")

    session_id: int


class CanvasMessageView(BaseModel):
    """画布会话消息列表项"""

    id: int
    role: int
    content: str
    input: dict | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str


class CanvasMessagesListRequest(BaseModel):
    """画布消息分页查询请求"""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    limit: int = Field(default=50, ge=1, le=200)
    before_id: int | None = None


class CanvasNodeGenerateResponse(BaseModel):
    """用户手动节点生成 JSON 响应"""

    node_id: str
    kind: NodeExecuteKind
    status: CanvasNodeStatus
    task_id: int | None = None
    node: CanvasNodeView
    error_message: str | None = None


__all__ = [
    "CanvasCancelRequest",
    "CanvasEdgeView",
    "CanvasMessageView",
    "CanvasMessagesListRequest",
    "CanvasNodeGenerateResponse",
    "CanvasNodeView",
    "CanvasPatchOp",
    "CanvasPatchRequest",
    "CanvasPatchResponse",
    "CanvasPosition",
    "CanvasReconnectRequest",
    "CanvasResumeRequest",
    "CanvasSessionCreateRequest",
    "CanvasSessionIdRequest",
    "CanvasSessionUpdateRequest",
    "CanvasSessionView",
    "CanvasSnapshot",
    "CanvasTurnRequest",
    "ConnectNodesOp",
    "CreateEdgePayload",
    "CreateNodeOp",
    "CreateNodePayload",
    "DeleteNodeOp",
    "DisconnectNodesOp",
    "GenerationProgress",
    "UpdateNodeOp",
    "UpdateNodePatch",
]
