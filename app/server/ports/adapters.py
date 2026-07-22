from __future__ import annotations

from uuid import UUID

from fastapi import UploadFile

from app.contracts.canvas import CanvasNodeView, CanvasPatchResponse
from app.server.assets.services.service import AssetService
from app.server.canvas.services import generation_sync
from app.server.canvas.services.canvas_service import canvas_service
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.generation.services.generate_task import GenerateTaskService, ObservedGenerateTask
from app.server.generation.services.generation_models import list_generate_models
from app.server.generation.services.generation_voices import resolve_tts_voice_id
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.ports.product import (
    AssetsPort,
    CanvasNodeRef,
    CanvasPort,
    ChatPort,
    GenerationPort,
    ObservedGenerationDTO,
)
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.schemas import (
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
)


class GenerationPortAdapter(GenerationPort, object):
    def __init__(self, service: GenerateTaskService) -> None:
        self._service = service

    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
        return await self._service.submit(user_id, req)

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerationDTO:
        observed = await self._service.observe_task(task_id, user_id)
        return ObservedGenerationDTO(task=observed.task, observation=observed.observation)

    async def assemble_task_view(self, observed: ObservedGenerationDTO, user_id: int) -> GenerateTaskView:
        wrapped = ObservedGenerateTask(task=observed.task, observation=observed.observation)
        return await self._service.assemble_task_view(wrapped, user_id)

    async def list_models(self, kind: str) -> GenerateModelsResponse:
        return await list_generate_models(kind)

    async def resolve_tts_voice_id(
        self,
        model_id: str,
        *,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
    ) -> str:
        return await resolve_tts_voice_id(
            model_id,
            voice_id=voice_id,
            fallback_voice_id=fallback_voice_id,
        )

    async def upload_material(self, user_id: int, file: UploadFile) -> GenerateMaterialUploadResponse:
        return await self._service.upload_material(user_id, file)


class CanvasPortAdapter(CanvasPort, object):
    async def project_from_task(self, task: GenerateTask) -> CanvasPatchResponse | None:
        return await generation_sync.project_from_task(task)

    async def get_node(self, project_id: int, node_id: str) -> CanvasNodeRef | None:
        try:
            node_uuid = UUID(node_id)
        except ValueError:
            return None
        node = await CanvasNodes.filter(
            id=node_uuid,
            project_id=project_id,
            deleted_at__isnull=True,
        ).first()
        if node is None:
            return None
        return CanvasNodeRef(
            node_id=str(node.id),
            task_id=node.task_id,
        )

    async def list_project_node_task_ids(self, project_id: int, *, limit: int) -> list[int]:
        rows = await CanvasNodes.filter(
            project_id=project_id,
            deleted_at__isnull=True,
            task_id__isnull=False,
        ).limit(limit)
        return [task_id for row in rows if (task_id := row.task_id) is not None]

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
    ) -> tuple[int, CanvasNodeView]:
        return await canvas_service.update_node_generation(
            project_id,
            node_id,
            task_id=task_id,
            status=status,
            output_asset_ids=output_asset_ids,
            error_message=error_message,
            expected_revision=expected_revision,
        )


class ChatPortAdapter(ChatPort, object):
    def __init__(self, attachment_repository: ChatAttachmentRepository) -> None:
        self._repo = attachment_repository

    async def get_attachment_storage_key(self, attachment_id: int, user_id: int) -> str | None:
        rows = await self._repo.get_by_ids_for_user([attachment_id], user_id)
        if not rows:
            return None
        return rows[0].storage_key


class AssetsPortAdapter(AssetsPort, object):
    def __init__(self, asset_service: AssetService, asset_repository: AssetRepository) -> None:
        self._assets = asset_service
        self._repo = asset_repository

    async def resolve_storage_key(self, asset_id: int, user_id: int) -> str | None:
        rows = await self._repo.get_active_by_ids_for_user([asset_id], user_id)
        if not rows:
            return None
        return rows[0].storage_key

    def build_url(self, storage_key: str) -> str:
        return self._assets.preview_url(storage_key)
