from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from tortoise.transactions import in_transaction

from app.contracts.canvas import CanvasNodeView, CanvasPatchOp, CanvasPatchResponse
from app.server.assets.services.service import AssetService
from app.server.canvas.services import generation_sync
from app.server.canvas.services.canvas_service import canvas_service, node_view_from_row
from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.messages import CanvasMessages
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.domain.models import GenerationModelCapabilities
from app.server.generation.services import GenerationService
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects
from app.server.projects.services.cover import cover_service
from app.server.ports.product import (
    AssetDTO,
    AssetsPort,
    CanvasEdgeDTO,
    CanvasGraphDTO,
    CanvasNodeClaimDTO,
    CanvasNodeDTO,
    CanvasPort,
    CanvasTaskProjectionDTO,
    ChatPort,
    GenerationTaskDTO,
    GenerationPort,
    ObservedGenerationDTO,
    ProjectPromptContextDTO,
)
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.schemas import (
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskSubmitResponse,
    SubmitGenerateRequest,
)


class GenerationPortAdapter(GenerationPort, object):
    def __init__(self, service: GenerationService) -> None:
        self._service = service

    async def submit(self, user_id: int, req: SubmitGenerateRequest) -> GenerateTaskSubmitResponse:
        return await self._service.submit(user_id, req)

    async def observe_task(self, task_id: int, user_id: int) -> ObservedGenerationDTO:
        observed = await self._service.observe_task(task_id, user_id)
        view = await self._service.assemble_task_view(observed, user_id)
        return ObservedGenerationDTO(task=_task_dto(observed.task), view=view)

    async def list_models(self, kind: GenerationKind) -> GenerateModelsResponse:
        return await self._service.list_models(kind)

    async def resolve_tts_voice_id(
        self,
        model_id: str,
        *,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
    ) -> str:
        return await self._service.resolve_tts_voice_id(
            model_id,
            voice_id=voice_id,
            fallback_voice_id=fallback_voice_id,
        )

    def get_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind | None = None,
    ) -> GenerationModelCapabilities | None:
        return self._service.get_model_capabilities(model_id, kind)

    async def require_model_capabilities(
        self,
        model_id: str,
        kind: GenerationKind,
    ) -> GenerationModelCapabilities:
        return await self._service.require_model_capabilities(model_id, kind)

    async def upload_material(
        self,
        user_id: int,
        *,
        filename: str,
        mime_type: str,
        raw_bytes: bytes,
    ) -> GenerateMaterialUploadResponse:
        return await self._service.upload_material(
            user_id,
            filename=filename,
            mime_type=mime_type,
            raw_bytes=raw_bytes,
        )


def _task_dto(task: GenerateTask) -> GenerationTaskDTO:
    raw_asset_ids = task.result_asset_ids
    asset_ids = tuple(int(item) for item in raw_asset_ids) if isinstance(raw_asset_ids, list) else ()
    return GenerationTaskDTO(
        id=int(task.id),
        user_id=int(task.user_id),
        status=int(task.status),
        result_asset_ids=asset_ids,
        error_message=task.error_message,
    )


def _node_dto(row: CanvasNodes, *, view: CanvasNodeView | None = None) -> CanvasNodeDTO:
    raw_asset_ids = row.output_asset_ids
    asset_ids = tuple(int(item) for item in raw_asset_ids) if isinstance(raw_asset_ids, list) else ()
    return CanvasNodeDTO(
        id=str(row.id),
        episode_id=int(row.episode_id),
        kind=CanvasNodeKind(row.kind),
        status=CanvasNodeStatus(row.status),
        position_x=float(row.position_x),
        position_y=float(row.position_y),
        title=row.title,
        input_prompt=row.input_prompt,
        output_text=row.output_text,
        model_id=row.model_id,
        voice_id=row.voice_id,
        ratio=row.ratio,
        duration_sec=row.duration_sec,
        resolution=row.resolution,
        task_id=int(row.task_id) if row.task_id is not None else None,
        output_asset_ids=asset_ids,
        output_asset_urls=tuple(view.output_asset_urls or ()) if view is not None else (),
        error_message=row.error_message,
    )


def _edge_dto(row: CanvasEdges) -> CanvasEdgeDTO:
    return CanvasEdgeDTO(
        id=str(row.id),
        source_node_id=str(row.source_node_id),
        target_node_id=str(row.target_node_id),
        source_port=CanvasSourcePort(row.source_port),
        target_port=CanvasTargetPort(row.target_port),
        edge_type=CanvasEdgeType(row.edge_type),
        metadata=dict(row.metadata or {}),
    )


class CanvasPortAdapter(CanvasPort, object):
    async def project_generation_task(self, task_id: int, user_id: int) -> CanvasTaskProjectionDTO | None:
        task = await GenerateTask.filter(
            id=task_id,
            user_id=user_id,
            deleted_at__isnull=True,
        ).first()
        if task is None:
            return None
        node = await CanvasNodes.filter(task_id=task_id, deleted_at__isnull=True).first()
        if node is None:
            return None
        patch = await generation_sync.project_from_task(task)
        task = await GenerateTask.filter(
            id=task_id,
            user_id=user_id,
            deleted_at__isnull=True,
        ).first()
        if task is None:
            return None
        node = await CanvasNodes.filter(task_id=task_id, deleted_at__isnull=True).first()
        if node is None:
            return None
        episode = await ProjectEpisodes.filter(id=node.episode_id, deleted_at__isnull=True).first()
        if episode is None:
            return None
        scope = CanvasScope(
            project_id=int(episode.project_id),
            episode_id=int(episode.id),
            user_id=user_id,
        )
        node_status = generation_sync.node_status_from_task(int(task.status))
        if node_status == CanvasNodeStatus.SUCCESS:
            await cover_service.fill_missing_cover_from_generation(
                scope,
                node_id=str(node.id),
                generation_task_id=task_id,
                asset_ids=list(_task_dto(task).result_asset_ids),
            )
        return CanvasTaskProjectionDTO(
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            node_id=str(node.id),
            task_id=task_id,
            user_id=user_id,
            status=node_status,
            patch=patch,
        )

    async def get_node(self, episode_id: int, node_id: str) -> CanvasNodeDTO | None:
        try:
            node_uuid = UUID(node_id)
        except ValueError:
            return None
        node = await CanvasNodes.filter(
            id=node_uuid,
            episode_id=episode_id,
            deleted_at__isnull=True,
        ).first()
        if node is None:
            return None
        return _node_dto(node)

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
    ) -> CanvasGraphDTO:
        meta = await CanvasEpisodeMeta.filter(episode_id=episode_id).first()
        if meta is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
        query = CanvasNodes.filter(episode_id=episode_id, deleted_at__isnull=True)
        if node_ids is not None:
            query = query.filter(id__in=[UUID(item) for item in node_ids])
        if kind is not None:
            query = query.filter(kind=kind.value)
        if status is not None:
            query = query.filter(status=status.value)
        rows = await query.all()
        views_by_id: dict[str, CanvasNodeView] = {}
        if include_asset_urls:
            views = [node_view_from_row(row) for row in rows]
            await canvas_service.refresh_node_asset_urls(
                views,
                scope=CanvasScope(project_id=project_id, episode_id=episode_id, user_id=user_id),
            )
            views_by_id = {str(view.id): view for view in views}
        edges = (
            await CanvasEdges.filter(episode_id=episode_id, deleted_at__isnull=True).all()
            if include_edges
            else []
        )
        return CanvasGraphDTO(
            revision=int(meta.revision),
            nodes=tuple(_node_dto(row, view=views_by_id.get(str(row.id))) for row in rows),
            edges=tuple(_edge_dto(row) for row in edges),
        )

    async def get_incoming_graph(self, episode_id: int, node_id: str) -> CanvasGraphDTO:
        node_uuid = UUID(node_id)
        target = await CanvasNodes.filter(
            id=node_uuid,
            episode_id=episode_id,
            deleted_at__isnull=True,
        ).first()
        if target is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
        edges = await CanvasEdges.filter(
            episode_id=episode_id,
            target_node_id=node_uuid,
            deleted_at__isnull=True,
        ).all()
        source_ids = [edge.source_node_id for edge in edges]
        sources = await CanvasNodes.filter(
            episode_id=episode_id,
            id__in=source_ids,
            deleted_at__isnull=True,
        ).all()
        meta = await CanvasEpisodeMeta.filter(episode_id=episode_id).first()
        if meta is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
        return CanvasGraphDTO(
            revision=int(meta.revision),
            nodes=tuple([_node_dto(target), *(_node_dto(row) for row in sources)]),
            edges=tuple(_edge_dto(row) for row in edges),
        )

    async def list_dependency_targets(self, episode_id: int, source_node_id: str) -> tuple[str, ...]:
        edges = await CanvasEdges.filter(
            episode_id=episode_id,
            source_node_id=UUID(source_node_id),
            deleted_at__isnull=True,
            edge_type=CanvasEdgeType.DEPENDENCY.value,
        ).all()
        return tuple(dict.fromkeys(str(edge.target_node_id) for edge in edges))

    async def get_project_prompt_context(
        self,
        project_id: int,
        episode_id: int,
    ) -> ProjectPromptContextDTO:
        project = await Projects.filter(id=project_id).first()
        if project is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        episode = await ProjectEpisodes.filter(
            id=episode_id,
            project_id=project_id,
            deleted_at__isnull=True,
        ).first()
        if episode is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
        return ProjectPromptContextDTO(
            project_id=project_id,
            episode_id=episode_id,
            project_name=project.name,
            episode_no=int(episode.episode_no),
            episode_name=episode.name,
            tone_constraint=(
                dict(project.tone_constraint) if isinstance(project.tone_constraint, dict) else None
            ),
            style_constraint=(
                dict(project.style_constraint) if isinstance(project.style_constraint, dict) else None
            ),
            config=dict(project.config) if isinstance(project.config, dict) else None,
        )

    async def apply_patch(
        self,
        *,
        project_id: int,
        episode_id: int,
        user_id: int,
        ops: list[CanvasPatchOp],
        expected_revision: int,
        turn_id: str | None,
    ) -> CanvasPatchResponse:
        return await canvas_service.apply_patch(
            CanvasScope(project_id=project_id, episode_id=episode_id, user_id=user_id),
            ops,
            expected_revision,
            turn_id=turn_id,
        )

    async def claim_node_for_generation(self, episode_id: int, node_id: str) -> CanvasNodeClaimDTO:
        async with in_transaction():
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
            if meta is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
            row = await CanvasNodes.select_for_update().filter(
                id=UUID(node_id),
                episode_id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if row is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
            active_task_id: int | None = None
            if row.task_id is not None:
                task = await GenerateTask.filter(id=row.task_id, deleted_at__isnull=True).first()
                if task is not None and GatewayTaskStatus(task.status).is_non_terminal:
                    active_task_id = int(task.id)
            if row.status == CanvasNodeStatus.RUNNING.value or active_task_id is not None:
                return CanvasNodeClaimDTO(claimed=False, active_task_id=active_task_id)
            row.status = CanvasNodeStatus.RUNNING.value
            row.error_message = ""
            await row.save()
            meta.revision = int(meta.revision) + 1
            await meta.save()
            return CanvasNodeClaimDTO(
                claimed=True,
                revision=int(meta.revision),
                node=node_view_from_row(row),
            )

    async def claim_workflow_node(
        self,
        episode_id: int,
        node_id: str,
        *,
        allowed_statuses: tuple[CanvasNodeStatus, ...],
    ) -> tuple[int, CanvasNodeView] | None:
        async with in_transaction():
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                # Episode gone (e.g. deleted): quiet skip, same as lost race.
                return None
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
            if meta is None:
                # Meta missing while episode exists is corruption, not a claim race.
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
            row = await CanvasNodes.select_for_update().filter(
                episode_id=episode_id,
                id=UUID(node_id),
                deleted_at__isnull=True,
                task_id__isnull=True,
                status__in=[status.value for status in allowed_statuses],
            ).first()
            if row is None:
                return None
            row.status = CanvasNodeStatus.RUNNING.value
            row.error_message = ""
            await row.save()
            meta.revision = int(meta.revision) + 1
            await meta.save()
            return int(meta.revision), node_view_from_row(row)

    async def list_episode_node_task_ids(self, episode_id: int, *, limit: int) -> list[int]:
        rows = await CanvasNodes.filter(
            episode_id=episode_id,
            deleted_at__isnull=True,
            task_id__isnull=False,
        ).limit(limit)
        return [task_id for row in rows if (task_id := row.task_id) is not None]

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
    ) -> tuple[int, CanvasNodeView]:
        return await canvas_service.update_node_generation(
            episode_id,
            node_id,
            task_id=task_id,
            status=status,
            output_asset_ids=output_asset_ids,
            error_message=error_message,
            model_id=model_id,
            voice_id=voice_id,
            duration_sec=duration_sec,
            ratio=ratio,
            resolution=resolution,
            expected_revision=expected_revision,
        )

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
    ) -> tuple[int, CanvasNodeView]:
        return await canvas_service.update_node_text_output(
            episode_id,
            node_id,
            status=status,
            output_text=output_text,
            error_message=error_message,
            model_id=model_id,
            expected_revision=expected_revision,
        )

    async def is_turn_completed(self, episode_id: int, client_turn_id: str) -> bool:
        row = await CanvasMessages.filter(
            episode_id=episode_id,
            role=2,
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        return row is not None

    async def append_canvas_message(
        self,
        *,
        episode_id: int,
        user_id: int,
        role: int,
        content: str,
        metadata: dict,
    ) -> None:
        await CanvasMessages.create(
            episode_id=episode_id,
            user_id=user_id,
            role=role,
            content=content,
            metadata=metadata,
        )

    async def find_user_turn_message(self, episode_id: int, client_turn_id: str) -> bool:
        row = await CanvasMessages.filter(
            episode_id=episode_id,
            role=1,
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        return row is not None

    async def touch_episode(self, episode_id: int) -> None:
        await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).update(
            updated_at=datetime.now(timezone.utc)
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

    async def list_assets(
        self,
        *,
        user_id: int,
        asset_type: str | None,
        source_type: str | None,
        limit: int,
    ) -> tuple[AssetDTO, ...]:
        query = self._repo.model.filter(user_id=user_id, deleted_at__isnull=True).order_by("-created_at")
        if asset_type is not None:
            query = query.filter(asset_type=asset_type)
        if source_type is not None:
            query = query.filter(source_type=source_type)
        return tuple(self._asset_dto(row) for row in await query.limit(limit))

    async def get_asset(self, *, user_id: int, asset_id: int) -> AssetDTO | None:
        rows = await self._repo.get_active_by_ids_for_user([asset_id], user_id)
        return self._asset_dto(rows[0]) if rows else None

    def _asset_dto(self, row) -> AssetDTO:
        view = self._assets.to_view(row)
        return AssetDTO(
            id=view.id,
            asset_type=view.asset_type,
            mime_type=view.mime_type,
            filename=view.filename,
            source_type=view.source_type,
            source_id=view.source_id,
            metadata=view.metadata,
            status=view.status,
            preview_url=view.preview_url,
        )
