from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from tortoise.transactions import in_transaction

from app.contracts.canvas import CanvasNodeView, CanvasPatchOp, CanvasPatchResponse, GenerationProgress
from app.server.assets.services.service import AssetService
from app.server.canvas.services import generation_sync
from app.server.canvas.services.canvas_service import canvas_service, node_view_from_row
from app.server.canvas.services.episode_events import (
    publish_patch_and_progress,
    publish_session_title,
)
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.canvas.domain.enums import (
    CanvasEdgeType,
    CanvasNodeKind,
    CanvasNodeStatus,
    CanvasSessionStatus,
    CanvasSourcePort,
    CanvasTargetPort,
)
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.messages import CanvasMessages
from app.server.canvas.persistence.sessions import CanvasSessions
from app.server.chat.domain.enums import ChatMessageRole
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
    SelectedSkillDTO,
    UpgradeInvitePort,
    UpgradeInviteProposalDTO,
    UserSkillPort,
    WorkshopPort,
    WorkshopRosterExpertDTO,
    WorkshopScheduleDTO,
    WorkshopWorkflowDTO,
    WorkshopWorkflowEdgeSpecDTO,
    WorkshopWorkflowNodeSpecDTO,
    WorkshopWorkflowRunDTO,
)
from app.server.skills.domain.enums import SkillScope, SkillSurface
from app.server.skills.domain.models import SelectedSkill
from app.server.skills.services.service import UserSkillService
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.chat.services.upgrade_invite import UpgradeInviteService
from app.server.generation.schemas import (
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskListRequest,
    GenerateTaskListResponse,
    GenerateTaskSubmitResponse,
    SubmitGenerateRequest,
)
from app.server.workshop.domain.enums import (
    WorkshopToolCapability,
)
from app.server.workshop.domain.workflow_definition import (
    NodeAssignee,
    WorkflowEdge,
    WorkflowNode,
)
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleError,
    WorkshopWorkflowScheduleService,
)
from app.server.workshop.services.workshop_project_service import WorkshopProjectService


_OPTIONAL_JSON_OBJECT = TypeAdapter(dict[str, Any] | None)


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

    async def list_tasks(
        self, user_id: int, req: GenerateTaskListRequest
    ) -> GenerateTaskListResponse:
        return await self._service.list_tasks(user_id, req)

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
    from app.server.canvas.domain.node_data import dump_node_data, parse_node_data

    data = dump_node_data(parse_node_data(row.data))
    return CanvasNodeDTO(
        id=str(row.id),
        episode_id=int(row.episode_id),
        kind=CanvasNodeKind(row.kind),
        revision=row.revision,
        position_x=float(row.position_x),
        position_y=float(row.position_y),
        width=float(row.width) if row.width is not None else None,
        height=float(row.height) if row.height is not None else None,
        data=data,
        output_asset_urls=tuple(view.output_asset_urls or ()) if view is not None else (),
    )


def _edge_dto(row: CanvasEdges) -> CanvasEdgeDTO:
    return CanvasEdgeDTO(
        id=str(row.id),
        revision=row.revision,
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
        node = await CanvasNodes.filter(
            deleted_at__isnull=True,
            data__contains={"generate_task_id": task_id},
        ).first()
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
        node = await CanvasNodes.filter(
            deleted_at__isnull=True,
            data__contains={"generate_task_id": task_id},
        ).first()
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
            query = query.filter(data__contains={"status": status.value})
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
            tone_constraint=_OPTIONAL_JSON_OBJECT.validate_python(project.tone_constraint),
            style_constraint=_OPTIONAL_JSON_OBJECT.validate_python(project.style_constraint),
            config=_OPTIONAL_JSON_OBJECT.validate_python(project.config),
        )

    async def apply_patch(
        self,
        *,
        project_id: int,
        episode_id: int,
        user_id: int,
        ops: list[CanvasPatchOp],
        turn_id: str | None,
    ) -> CanvasPatchResponse:
        await canvas_episode_fence.assert_writable(episode_id)
        return await canvas_service.apply_patch(
            CanvasScope(project_id=project_id, episode_id=episode_id, user_id=user_id),
            ops,
            turn_id=turn_id,
        )

    async def claim_node_for_generation(self, episode_id: int, node_id: str) -> CanvasNodeClaimDTO:
        await canvas_episode_fence.assert_writable(episode_id)
        async with in_transaction():
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            # meta 无字段变更; 仍加锁以保持与 apply_patch 一致的锁顺序
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
            from app.server.canvas.domain.node_data import (
                apply_generation_to_data,
                data_status,
                dump_node_data,
                parse_node_data,
            )

            existing = parse_node_data(row.data)
            active_task_id: int | None = None
            if existing.generate_task_id is not None:
                task = await GenerateTask.filter(
                    id=existing.generate_task_id,
                    deleted_at__isnull=True,
                ).first()
                if task is not None and GatewayTaskStatus(task.status).is_non_terminal:
                    active_task_id = task.id
            if data_status(existing) == CanvasNodeStatus.RUNNING or active_task_id is not None:
                return CanvasNodeClaimDTO(claimed=False, active_task_id=active_task_id)
            claimed = apply_generation_to_data(
                existing,
                status=CanvasNodeStatus.RUNNING,
                generate_task_id=None,
                generate_error="",
            )
            row.data = dump_node_data(claimed)
            row.revision = row.revision + 1
            await row.save()
            return CanvasNodeClaimDTO(
                claimed=True,
                revision=row.revision,
                node=node_view_from_row(row),
            )

    async def claim_workflow_node(
        self,
        episode_id: int,
        node_id: str,
        *,
        allowed_statuses: tuple[CanvasNodeStatus, ...],
    ) -> tuple[int, CanvasNodeView] | None:
        await canvas_episode_fence.assert_writable(episode_id)
        async with in_transaction():
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                # episode 已删除等: 静默跳过, 与抢占失败同语义
                return None
            # meta 无字段变更; 仍加锁以保持与 apply_patch 一致的锁顺序
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
            if meta is None:
                # episode 在而 meta 缺失属于数据损坏, 不是 claim 竞态
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
            row = await CanvasNodes.select_for_update().filter(
                episode_id=episode_id,
                id=UUID(node_id),
                deleted_at__isnull=True,
            ).first()
            if row is None:
                return None
            from app.server.canvas.domain.node_data import (
                apply_generation_to_data,
                data_status,
                dump_node_data,
                parse_node_data,
            )

            existing = parse_node_data(row.data)
            if existing.generate_task_id is not None:
                return None
            if data_status(existing) not in allowed_statuses:
                return None
            claimed = apply_generation_to_data(
                existing,
                status=CanvasNodeStatus.RUNNING,
                generate_error="",
            )
            row.data = dump_node_data(claimed)
            row.revision = row.revision + 1
            await row.save()
            return row.revision, node_view_from_row(row)

    async def list_episode_node_task_ids(self, episode_id: int, *, limit: int) -> list[int]:
        """列出本集节点上的 generate_task_id（有任务的节点，按 updated_at 新到旧）

        在 DB 侧过滤含 generate_task_id 的行，避免 episode 全表进内存
        """
        if limit < 1:
            return []
        from tortoise import connections

        conn = connections.get("default")
        dialect = getattr(conn.capabilities, "dialect", "") or ""
        if dialect == "sqlite":
            rows = await conn.execute_query_dict(
                """
                SELECT CAST(json_extract(data, '$.generate_task_id') AS INTEGER) AS task_id
                FROM canvas_nodes
                WHERE episode_id = ?
                  AND deleted_at IS NULL
                  AND json_extract(data, '$.generate_task_id') IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [episode_id, limit],
            )
        elif dialect in {"postgres", "postgresql"}:
            rows = await conn.execute_query_dict(
                """
                SELECT (data->>'generate_task_id')::bigint AS task_id
                FROM canvas_nodes
                WHERE episode_id = $1
                  AND deleted_at IS NULL
                  AND jsonb_typeof(data->'generate_task_id') = 'number'
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                [episode_id, limit],
            )
        else:
            raise AppError(
                ErrorCode.INTERNAL,
                f"list_episode_node_task_ids unsupported db dialect: {dialect!r}",
            )
        task_ids: list[int] = []
        for row in rows:
            raw = row.get("task_id")
            if raw is None:
                continue
            task_ids.append(int(raw))
        return task_ids

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
        await canvas_episode_fence.assert_writable(episode_id)
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
        await canvas_episode_fence.assert_writable(episode_id)
        return await canvas_service.update_node_text_output(
            episode_id,
            node_id,
            status=status,
            output_text=output_text,
            error_message=error_message,
            model_id=model_id,
            expected_revision=expected_revision,
        )

    async def is_turn_completed(self, session_id: int, client_turn_id: str) -> bool:
        row = await CanvasMessages.filter(
            session_id=session_id,
            role=ChatMessageRole.ASSISTANT,
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        return row is not None

    async def append_canvas_message(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
        role: ChatMessageRole,
        content: str,
        metadata: dict,
    ) -> None:
        session = await CanvasSessions.get_or_none(
            id=session_id,
            episode_id=episode_id,
            user_id=user_id,
        )
        if session is None or session.status != CanvasSessionStatus.ACTIVE:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas session not found")
        await CanvasMessages.create(
            episode_id=episode_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            metadata=metadata,
        )

    async def find_user_turn_message(self, session_id: int, client_turn_id: str) -> bool:
        row = await CanvasMessages.filter(
            session_id=session_id,
            role=ChatMessageRole.USER,
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        return row is not None

    async def get_user_turn_input(self, session_id: int, client_turn_id: str) -> dict | None:
        row = await CanvasMessages.filter(
            session_id=session_id,
            role=ChatMessageRole.USER,
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        if row is None:
            return None
        raw = (row.metadata or {}).get("input")
        return _OPTIONAL_JSON_OBJECT.validate_python(raw)

    async def touch_episode(self, episode_id: int) -> None:
        await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).update(
            updated_at=datetime.now(timezone.utc)
        )

    async def get_session_title(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
    ) -> str | None:
        row = await CanvasSessions.get_or_none(
            id=session_id,
            episode_id=episode_id,
            user_id=user_id,
            status=CanvasSessionStatus.ACTIVE,
        )
        return None if row is None else row.title

    async def count_session_user_messages(self, session_id: int) -> int:
        return await CanvasMessages.filter(
            session_id=session_id,
            role=ChatMessageRole.USER,
        ).count()

    async def touch_session(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
    ) -> None:
        await CanvasSessions.filter(
            id=session_id,
            episode_id=episode_id,
            user_id=user_id,
            status=CanvasSessionStatus.ACTIVE,
        ).update(updated_at=datetime.now(timezone.utc))

    async def apply_session_title_if_unchanged(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
        expected_title: str,
        new_title: str,
    ) -> tuple[bool, str | None]:
        row = await CanvasSessions.get_or_none(
            id=session_id,
            episode_id=episode_id,
            user_id=user_id,
            status=CanvasSessionStatus.ACTIVE,
        )
        if row is None or row.title != expected_title:
            return False, None
        row.title = new_title
        await row.save(update_fields=["title", "updated_at"])
        updated_at = row.updated_at.isoformat() if row.updated_at else None
        return True, updated_at

    async def publish_episode_graph_event(
        self,
        episode_id: int,
        *,
        canvas_patch: CanvasPatchResponse | None = None,
        progress: GenerationProgress | None = None,
    ) -> None:
        await publish_patch_and_progress(
            episode_id,
            canvas_patch=canvas_patch,
            progress=progress,
        )

    async def publish_episode_session_title(
        self,
        episode_id: int,
        *,
        session_id: int,
        title: str,
        updated_at: str,
    ) -> None:
        await publish_session_title(
            episode_id,
            session_id=session_id,
            title=title,
            updated_at=updated_at,
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


class UserSkillPortAdapter:
    """用户技能 Port 适配器"""

    def __init__(self, service: UserSkillService) -> None:
        self._service = service

    async def list_enabled_for_index(
        self,
        *,
        surface: str,
        user_id: int,
        project_id: int | None,
    ) -> tuple[SelectedSkillDTO, ...]:
        """列出启用技能索引"""
        items = await self._service.list_enabled_files_for_index(
            surface=SkillSurface(surface),
            user_id=user_id,
            project_id=project_id,
        )
        return tuple(_selected_skill_dto(item) for item in items)

    async def resolve_selected(
        self,
        *,
        surface: str,
        user_id: int,
        project_id: int | None,
        paths: list[str],
    ) -> tuple[SelectedSkillDTO, ...]:
        """解析 turn 显式引用的技能"""
        items = await self._service.resolve_selected_skills(
            SkillSurface(surface),
            user_id,
            project_id,
            paths,
        )
        return tuple(_selected_skill_dto(item) for item in items)

    async def write_user_file(
        self,
        *,
        surface: str,
        user_id: int,
        path: str,
        name: str,
        description: str | None,
        content: str,
        revision: int | None,
    ) -> SelectedSkillDTO:
        """写入 user 域技能文件并返回最新正文"""
        detail = await self._service.write_user_file_detail(
            user_id,
            SkillSurface(surface),
            path,
            name,
            content,
            description,
            revision,
        )
        return SelectedSkillDTO(
            path=detail.meta.path,
            scope=str(SkillScope.USER),
            content=detail.content,
            description=detail.meta.description,
            revision=int(detail.meta.revision),
        )

    async def get_user_file(
        self,
        *,
        surface: str,
        user_id: int,
        path: str,
    ) -> SelectedSkillDTO | None:
        """读取 user 域技能文件, 不存在返回 None"""
        try:
            detail = await self._service.get_file(
                user_id,
                SkillSurface(surface),
                SkillScope.USER,
                path,
                None,
            )
        except AppError as exc:
            if exc.code == int(ErrorCode.RESOURCE_NOT_FOUND):
                return None
            raise
        return SelectedSkillDTO(
            path=detail.meta.path,
            scope=str(SkillScope.USER),
            content=detail.content,
            description=detail.meta.description,
            revision=int(detail.meta.revision),
        )


def _selected_skill_dto(item: SelectedSkill) -> SelectedSkillDTO:
    """领域 SelectedSkill 转为 Port DTO"""
    return SelectedSkillDTO(
        path=item.path,
        scope=str(item.scope),
        content=item.content,
        description=item.description,
        revision=item.revision,
    )


class WorkshopPortAdapter:
    """工坊项目 / 工作流 Port 适配器"""

    def __init__(
        self,
        projects: WorkshopProjectService,
        schedules: WorkshopWorkflowScheduleService,
    ) -> None:
        """注入项目服务与工作流调度服务"""
        self._projects = projects
        self._schedules = schedules

    async def add_preset_to_roster(
        self,
        *,
        project_id: str,
        user_id: int,
        preset_key: str,
    ) -> WorkshopRosterExpertDTO:
        """将预置专家加入名册"""
        expert = await self._projects.add_preset_to_roster(
            project_id=project_id,
            user_id=user_id,
            preset_key=preset_key,
        )
        return WorkshopRosterExpertDTO(
            id=expert.id,
            name=expert.name,
            preset_key=expert.preset_key,
        )

    async def invite_to_room(
        self, *, project_id: str, user_id: int, expert_id: str
    ) -> None:
        """邀请专家进房"""
        await self._projects.invite_to_room(
            project_id=project_id, user_id=user_id, expert_id=expert_id
        )

    async def room_members(self, *, project_id: str, user_id: int) -> frozenset[str]:
        """列出房间在场专家 id"""
        members = await self._projects.room_members(
            project_id=project_id, user_id=user_id
        )
        return frozenset(members)

    async def agent_draft_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        model_key: str,
        nodes: tuple[WorkshopWorkflowNodeSpecDTO, ...],
        edges: tuple[WorkshopWorkflowEdgeSpecDTO, ...],
    ) -> WorkshopWorkflowDTO:
        """Agent 起草工作流草稿"""
        domain_nodes = tuple(_node_from_spec(item) for item in nodes)
        domain_edges = tuple(
            WorkflowEdge(from_id=item.from_id, to_id=item.to_id) for item in edges
        )
        try:
            record = await self._schedules.agent_draft_workflow(
                project_id=project_id,
                user_id=user_id,
                name=name,
                nodes=domain_nodes,
                edges=domain_edges,
                model_key=model_key,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return _workflow_dto(record)

    async def confirm_save_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> WorkshopWorkflowDTO:
        """确认保存工作流"""
        try:
            record = await self._schedules.user_confirm_save_workflow(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return _workflow_dto(record)

    async def create_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        cron: str,
        timezone: str,
        authorized_capabilities: tuple[str, ...],
    ) -> WorkshopScheduleDTO:
        """创建定时"""
        caps = _parse_capabilities(authorized_capabilities)
        try:
            record = await self._schedules.create_schedule(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
                cron=cron,
                timezone=timezone,
                authorized_capabilities=caps,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return WorkshopScheduleDTO(
            id=record.id,
            workflow_id=record.workflow_id,
            cron=record.cron,
            timezone=record.timezone,
            enabled=record.enabled,
        )

    async def manual_run_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        authorized_capabilities: tuple[str, ...],
    ) -> WorkshopWorkflowRunDTO:
        """手动跑一次已保存工作流"""
        caps = _parse_capabilities(authorized_capabilities)
        try:
            result = await self._schedules.manual_run_with_light_confirm(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
                authorized_capabilities=caps,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return WorkshopWorkflowRunDTO(
            id=result.run.id,
            workflow_id=result.run.workflow_id,
            status=result.run.status.value,
        )

    async def start_workflow_execution(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> tuple[WorkshopScheduleDTO, ...]:
        """开启工作流执行（启用关联 schedule）"""
        try:
            records = await self._schedules.start_workflow_execution(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return tuple(_schedule_dto(item) for item in records)

    async def stop_workflow_execution(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> tuple[WorkshopScheduleDTO, ...]:
        """停止工作流执行（禁用关联 schedule）"""
        try:
            records = await self._schedules.stop_workflow_execution(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc
        return tuple(_schedule_dto(item) for item in records)

    async def delete_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> None:
        """删除工作流"""
        try:
            await self._schedules.delete_workflow(
                project_id=project_id,
                user_id=user_id,
                workflow_id=workflow_id,
            )
        except WorkshopWorkflowScheduleError as exc:
            raise ValueError(str(exc)) from exc


def _schedule_dto(record) -> WorkshopScheduleDTO:
    """定时读模型转 Port DTO"""
    return WorkshopScheduleDTO(
        id=record.id,
        workflow_id=record.workflow_id,
        cron=record.cron,
        timezone=record.timezone,
        enabled=record.enabled,
    )


def _workflow_dto(record) -> WorkshopWorkflowDTO:
    """领域工作流转 DTO"""
    return WorkshopWorkflowDTO(
        id=record.id,
        name=record.name,
        status=record.status.value,
        model_key=record.model_key,
        revision=record.revision,
    )


def _parse_capabilities(
    raw: tuple[str, ...],
) -> tuple[WorkshopToolCapability, ...]:
    """解析能力字面量"""
    return tuple(WorkshopToolCapability(item) for item in raw)


def _node_from_spec(spec: WorkshopWorkflowNodeSpecDTO) -> WorkflowNode:
    """Port 节点规格转领域节点；交付文件由执行面收口，不预声明硬端口"""
    caps = tuple(
        WorkshopToolCapability(item) for item in spec.external_capabilities
    )
    return WorkflowNode(
        id=spec.id,
        title=spec.title,
        instruction=spec.instruction,
        assignee=NodeAssignee(preset_key=spec.preset_key),
        outputs=(),
        external_capabilities=caps,
    )


class UpgradeInvitePortAdapter:
    """升级邀请 Port 适配器"""

    def __init__(self, service: UpgradeInviteService) -> None:
        """注入升级邀请服务"""
        self._service = service

    async def create_proposal(
        self,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        source_user_text: str,
        expert_keys: tuple[str, ...],
        primary_expert_key: str,
        rationale: str,
        host_narration: str,
    ) -> UpgradeInviteProposalDTO:
        """创建待确认升级邀请提议"""
        record = await self._service.create_proposal(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            source_user_text=source_user_text,
            expert_keys=expert_keys,
            primary_expert_key=primary_expert_key,
            rationale=rationale,
            host_narration=host_narration,
        )
        return UpgradeInviteProposalDTO(
            id=record.id,
            conversation_id=record.conversation_id,
            expert_keys=record.expert_keys,
            primary_expert_key=record.primary_expert_key,
            rationale=record.rationale,
            host_narration=record.host_narration,
        )
