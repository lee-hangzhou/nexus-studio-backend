from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from app.contracts.canvas import (
    NODE_DATA_CLIENT_FORBIDDEN_KEYS,
    CanvasNodeData,
    CanvasPosition,
)
from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasPatchOperation,
    CanvasSourcePort,
    CanvasTargetPort,
)


class PatchLlmContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


def _clone_optional_field(info: FieldInfo) -> FieldInfo:
    """保留原 Field 约束（ge/le/description 等），统一默认 None 供 LLM 省略"""
    return FieldInfo.merge_field_infos(info, FieldInfo(default=None))


def _build_patch_node_data_llm() -> type[BaseModel]:
    """从 CanvasNodeData 派生 LLM 可写子集，自动排除投影字段，避免双源漂移"""
    field_defs: dict[str, Any] = {}
    for name, info in CanvasNodeData.model_fields.items():
        if name in NODE_DATA_CLIENT_FORBIDDEN_KEYS:
            continue
        field_defs[name] = (info.annotation, _clone_optional_field(info))
    return create_model(
        "PatchNodeDataLlm",
        __base__=PatchLlmContract,
        __module__=__name__,
        **field_defs,
    )


PatchNodeDataLlm = _build_patch_node_data_llm()


class PatchCreateNodePayloadLlm(PatchLlmContract):
    kind: CanvasNodeKind
    position: CanvasPosition
    width: float | None = None
    height: float | None = None
    data: PatchNodeDataLlm = Field(default_factory=PatchNodeDataLlm)  # type: ignore[valid-type]


class PatchUpdateNodePayloadLlm(PatchLlmContract):
    id: str
    revision: int = Field(ge=1)
    position: CanvasPosition | None = None
    width: float | None = None
    height: float | None = None
    data: PatchNodeDataLlm | None = None  # type: ignore[valid-type]


class PatchCreateNodeOpLlm(PatchLlmContract):
    op: Literal[CanvasPatchOperation.CREATE_NODE]
    node: PatchCreateNodePayloadLlm


class PatchUpdateNodeOpLlm(PatchLlmContract):
    op: Literal[CanvasPatchOperation.UPDATE_NODE]
    node: PatchUpdateNodePayloadLlm


PatchNodeOperationLlm = Annotated[
    PatchCreateNodeOpLlm | PatchUpdateNodeOpLlm,
    Field(discriminator="op"),
]


class PatchCreateEdgePayloadLlm(PatchLlmContract):
    source: str
    target: str
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType = CanvasEdgeType.DEPENDENCY
    metadata: dict = Field(default_factory=dict)


class PatchConnectOpLlm(PatchLlmContract):
    op: Literal[CanvasPatchOperation.CONNECT]
    edge: PatchCreateEdgePayloadLlm


class PatchDisconnectOpLlm(PatchLlmContract):
    op: Literal[CanvasPatchOperation.DISCONNECT]
    edge_id: str
    expected_revision: int = Field(ge=1)


PatchEdgeOperationLlm = Annotated[
    PatchConnectOpLlm | PatchDisconnectOpLlm,
    Field(discriminator="op"),
]
