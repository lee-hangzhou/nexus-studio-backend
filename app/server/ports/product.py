from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.contracts.canvas import CanvasNodeView, CanvasPatchOp, CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.chat.domain.enums import ChatMessageRole
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.domain.models import GenerationModelCapabilities
from app.server.generation.schemas import (
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)


@dataclass(frozen=True)
class GenerationTaskDTO:
    id: int
    user_id: int
    status: int
    result_asset_ids: tuple[int, ...]
    error_message: str | None


@dataclass(frozen=True)
class ObservedGenerationDTO:
    task: GenerationTaskDTO
    view: GenerateTaskView


@dataclass(frozen=True)
class CanvasNodeDTO:
    id: str
    episode_id: int
    kind: CanvasNodeKind
    status: CanvasNodeStatus
    revision: int
    position_x: float
    position_y: float
    title: str
    input_prompt: str
    output_text: str
    model_id: str | None
    voice_id: str | None
    ratio: str | None
    duration_sec: int | None
    resolution: str | None
    task_id: int | None
    output_asset_ids: tuple[int, ...]
    output_asset_urls: tuple[str, ...]
    error_message: str | None


@dataclass(frozen=True)
class CanvasEdgeDTO:
    id: str
    revision: int
    source_node_id: str
    target_node_id: str
    source_port: CanvasSourcePort
    target_port: CanvasTargetPort
    edge_type: CanvasEdgeType
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CanvasGraphDTO:
    nodes: tuple[CanvasNodeDTO, ...]
    edges: tuple[CanvasEdgeDTO, ...]


@dataclass(frozen=True)
class CanvasNodeClaimDTO:
    claimed: bool
    active_task_id: int | None = None
    revision: int | None = None
    node: CanvasNodeView | None = None


@dataclass(frozen=True)
class CanvasTaskProjectionDTO:
    project_id: int
    episode_id: int
    node_id: str
    task_id: int
    user_id: int
    status: CanvasNodeStatus
    patch: CanvasPatchResponse | None


@dataclass(frozen=True)
class ProjectPromptContextDTO:
    project_id: int
    episode_id: int
    project_name: str | None = None
    episode_no: int | None = None
    episode_name: str | None = None
    tone_constraint: dict[str, Any] | None = None
    style_constraint: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


@dataclass(frozen=True)
class AssetDTO:
    id: int
    asset_type: str
    mime_type: str
    filename: str
    source_type: str
    source_id: str | None
    metadata: dict[str, Any]
    status: str
    preview_url: str


@runtime_checkable
class GenerationPort(Protocol):
    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse: ...

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerationDTO: ...

    async def list_models(self, kind: GenerationKind) -> GenerateModelsResponse: ...

    async def resolve_tts_voice_id(
        self,
        model_id: str,
        *,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
    ) -> str: ...

    def get_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind | None = None,
    ) -> GenerationModelCapabilities | None: ...

    async def require_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities: ...

    async def upload_material(
        self,
        user_id: int,
        *,
        filename: str,
        mime_type: str,
        raw_bytes: bytes,
    ) -> GenerateMaterialUploadResponse: ...


@runtime_checkable
class CanvasPort(Protocol):
    async def project_generation_task(self, task_id: int, user_id: int) -> CanvasTaskProjectionDTO | None: ...

    async def get_node(self, episode_id: int, node_id: str) -> CanvasNodeDTO | None: ...

    async def get_graph(
        self,
        *,
        project_id: int,
        episode_id: int,
        user_id: int,
        node_ids: tuple[str, ...] | None = None,
        kind: CanvasNodeKind | None = None,
        status: CanvasNodeStatus | None = None,
        include_edges: bool = False,
        include_asset_urls: bool = False,
    ) -> CanvasGraphDTO: ...

    async def get_incoming_graph(self, episode_id: int, node_id: str) -> CanvasGraphDTO: ...

    async def list_dependency_targets(self, episode_id: int, source_node_id: str) -> tuple[str, ...]: ...

    async def list_episode_node_task_ids(self, episode_id: int, *, limit: int) -> list[int]: ...

    async def get_project_prompt_context(
        self,
        project_id: int,
        episode_id: int,
    ) -> ProjectPromptContextDTO: ...

    async def apply_patch(
        self,
        *,
        project_id: int,
        episode_id: int,
        user_id: int,
        ops: list[CanvasPatchOp],
        turn_id: str | None,
    ) -> CanvasPatchResponse: ...

    async def claim_node_for_generation(self, episode_id: int, node_id: str) -> CanvasNodeClaimDTO: ...

    async def claim_workflow_node(
        self,
        episode_id: int,
        node_id: str,
        *,
        allowed_statuses: tuple[CanvasNodeStatus, ...],
    ) -> tuple[int, CanvasNodeView] | None: ...

    async def update_node_generation(
        self,
        episode_id: int,
        node_id: str,
        *,
        task_id: int | None,
        status: CanvasNodeStatus,
        output_asset_ids: list[int] | None = None,
        error_message: str | None = None,
        model_id: str | None = None,
        voice_id: str | None = None,
        duration_sec: int | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[int, CanvasNodeView]: ...

    async def update_node_text_output(
        self,
        episode_id: int,
        node_id: str,
        *,
        status: CanvasNodeStatus,
        output_text: str | None = None,
        error_message: str | None = None,
        model_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[int, CanvasNodeView]: ...

    async def is_turn_completed(self, session_id: int, client_turn_id: str) -> bool: ...

    async def append_canvas_message(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
        role: ChatMessageRole,
        content: str,
        metadata: dict[str, Any],
    ) -> None: ...

    async def find_user_turn_message(self, session_id: int, client_turn_id: str) -> bool: ...

    async def touch_episode(self, episode_id: int) -> None: ...

    async def get_session_title(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
    ) -> str | None: ...

    async def count_session_user_messages(self, session_id: int) -> int: ...

    async def touch_session(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
    ) -> None: ...

    async def apply_session_title_if_unchanged(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
        expected_title: str,
        new_title: str,
    ) -> tuple[bool, str | None]: ...

    async def publish_episode_graph_event(
        self,
        episode_id: int,
        *,
        canvas_patch: CanvasPatchResponse | None = None,
        progress: GenerationProgress | None = None,
    ) -> None: ...

    async def publish_episode_session_title(
        self,
        episode_id: int,
        *,
        session_id: int,
        title: str,
        updated_at: str,
    ) -> None: ...


@runtime_checkable
class ChatPort(Protocol):
    async def get_attachment_storage_key(self, attachment_id: int, user_id: int) -> str | None: ...


@runtime_checkable
class AssetsPort(Protocol):
    async def resolve_storage_key(self, asset_id: int, user_id: int) -> str | None: ...

    def build_url(self, storage_key: str) -> str: ...

    async def list_assets(
        self,
        *,
        user_id: int,
        asset_type: str | None,
        source_type: str | None,
        limit: int,
    ) -> tuple[AssetDTO, ...]: ...

    async def get_asset(self, *, user_id: int, asset_id: int) -> AssetDTO | None: ...
