from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.contracts.canvas import CanvasNodeView, CanvasPatchResponse
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.domain.models import GenerationModelCapabilities
from app.server.generation.schemas.observation import GatewayQueueObservation
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.generation.schemas import (
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)


@dataclass(frozen=True)
class ObservedGenerationDTO:
    task: GenerateTask
    observation: GatewayQueueObservation | None = None


@dataclass(frozen=True)
class CanvasNodeRef:
    node_id: str
    task_id: int | None


@runtime_checkable
class GenerationPort(Protocol):
    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse: ...

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerationDTO: ...

    async def assemble_task_view(self, observed: ObservedGenerationDTO, user_id: int) -> GenerateTaskView: ...

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
    async def project_from_task(self, task: GenerateTask) -> CanvasPatchResponse | None: ...

    async def get_node(self, project_id: int, node_id: str) -> CanvasNodeRef | None: ...

    async def list_project_node_task_ids(self, project_id: int, *, limit: int) -> list[int]: ...

    async def update_node_generation(
        self,
        project_id: int,
        node_id: str,
        *,
        task_id: int | None,
        status: CanvasNodeStatus,
        output_asset_ids: list[int] | None = None,
        error_message: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[int, CanvasNodeView]: ...


@runtime_checkable
class ChatPort(Protocol):
    async def get_attachment_storage_key(self, attachment_id: int, user_id: int) -> str | None: ...


@runtime_checkable
class AssetsPort(Protocol):
    async def resolve_storage_key(self, asset_id: int, user_id: int) -> str | None: ...

    def build_url(self, storage_key: str) -> str: ...
