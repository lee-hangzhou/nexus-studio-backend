from __future__ import annotations

from datetime import datetime, timezone

from tortoise.transactions import in_transaction

from app.server.canvas.persistence.episode_lifecycle import CanvasEpisodeLifecycleRepository
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.persistence.repository import GenerateTaskRepository
from app.server.generation.services.task_state import GenerationTaskStateService
from app.server.projects import assembly
from app.server.projects.domain.models import NameAndCoverUpdate, UpdateField
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects
from app.server.projects.persistence.repositories import EpisodeRepository, ProjectRepository
from app.server.projects.schemas import (
    EpisodeView,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectView,
)
from app.server.projects.services.cover import CoverService, cover_service
from app.server.projects.services.scope import CanvasScopeService

DEFAULT_EPISODE_NAME = "第 1 集"


class ProjectService:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        episodes: EpisodeRepository,
        canvas_lifecycle: CanvasEpisodeLifecycleRepository,
        covers: CoverService,
    ) -> None:
        self._projects = projects
        self._episodes = episodes
        self._canvas_lifecycle = canvas_lifecycle
        self._covers = covers

    async def _require_owned(self, user_id: int, project_id: int) -> Projects:
        row = await self._projects.get_owned(user_id, project_id)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        return row

    async def _view(
        self,
        row: Projects,
        *,
        user_id: int,
        episode_count: int,
        activity_at: datetime,
    ) -> ProjectView:
        covers = await self._covers.cover_views_by_id([row.cover_asset_id], user_id=user_id)
        return assembly.project_view(
            row,
            cover=covers.get(int(row.cover_asset_id)) if row.cover_asset_id is not None else None,
            episode_count=episode_count,
            activity_at=activity_at,
        )

    async def list_for_user(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 11,
        query_text: str = "",
    ) -> tuple[list[ProjectView], int]:
        rows, total = await self._projects.list_owned_with_activity(
            user_id,
            page=page,
            page_size=page_size,
            query_text=query_text,
        )
        covers = await self._covers.cover_views_by_id(
            (row.project.cover_asset_id for row in rows),
            user_id=user_id,
        )
        return assembly.project_activity_views(rows, covers_by_id=covers), total

    async def get_for_user(self, user_id: int, project_id: int) -> ProjectDetailResponse:
        project = await self._require_owned(user_id, project_id)
        total = await self._episodes.count_active(project_id)
        episodes, _ = await self._episodes.list_active(project_id, page=1, page_size=max(total, 1))
        activity_at = max(
            [project.updated_at or project.created_at or datetime.now(timezone.utc)]
            + [episode.updated_at for episode in episodes if episode.updated_at is not None]
        )
        cover_ids = [project.cover_asset_id, *(episode.cover_asset_id for episode in episodes)]
        covers = await self._covers.cover_views_by_id(cover_ids, user_id=user_id)
        project_cover = covers.get(int(project.cover_asset_id)) if project.cover_asset_id is not None else None
        return ProjectDetailResponse(
            project=assembly.project_view(
                project,
                cover=project_cover,
                episode_count=total,
                activity_at=activity_at,
            ),
            episodes=assembly.episode_views(episodes, covers_by_id=covers),
        )

    async def create(self, user_id: int, name: str) -> ProjectCreateResponse:
        project_name = name.strip()
        if not project_name:
            raise AppError(ErrorCode.INVALID_PARAMS, "project name required")
        async with in_transaction():
            project = await self._projects.create(user_id, project_name)
            episode = await self._episodes.create(
                project_id=int(project.id),
                creator_id=user_id,
                episode_no=1,
                name=DEFAULT_EPISODE_NAME,
            )
            await self._canvas_lifecycle.create_empty(int(episode.id))
        activity_at = episode.updated_at or episode.created_at or datetime.now(timezone.utc)
        return ProjectCreateResponse(
            project=assembly.project_view(
                project,
                cover=None,
                episode_count=1,
                activity_at=activity_at,
            ),
            default_episode=assembly.episode_view(episode, cover=None),
        )

    async def update(
        self,
        user_id: int,
        project_id: int,
        changes: NameAndCoverUpdate,
    ) -> ProjectView:
        await self._require_owned(user_id, project_id)
        normalized = await _normalize_update(changes, user_id=user_id, covers=self._covers)
        row = await self._projects.update_name_and_cover(project_id, changes=normalized)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        activity_at = row.updated_at or row.created_at or datetime.now(timezone.utc)
        return await self._view(
            row,
            user_id=user_id,
            episode_count=await self._episodes.count_active(project_id),
            activity_at=activity_at,
        )


class EpisodeService:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        episodes: EpisodeRepository,
        canvas_lifecycle: CanvasEpisodeLifecycleRepository,
        generation_tasks: GenerationTaskStateService,
        scopes: CanvasScopeService,
        covers: CoverService,
    ) -> None:
        self._projects = projects
        self._episodes = episodes
        self._canvas_lifecycle = canvas_lifecycle
        self._generation_tasks = generation_tasks
        self._scopes = scopes
        self._covers = covers

    async def _views(self, rows: list[ProjectEpisodes], *, user_id: int) -> list[EpisodeView]:
        covers = await self._covers.cover_views_by_id(
            (row.cover_asset_id for row in rows),
            user_id=user_id,
        )
        return assembly.episode_views(rows, covers_by_id=covers)

    async def create(self, user_id: int, project_id: int, name: str | None = None) -> EpisodeView:
        async with in_transaction():
            project = await self._projects.get_owned_for_update(user_id, project_id)
            if project is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
            episode_no = await self._episodes.max_episode_no(project_id) + 1
            clean_name = name.strip() if name is not None and name.strip() else f"第 {episode_no} 集"
            if len(clean_name) > 255:
                raise AppError(ErrorCode.INVALID_PARAMS, "episode name too long")
            episode = await self._episodes.create(
                project_id=project_id,
                creator_id=user_id,
                episode_no=episode_no,
                name=clean_name,
            )
            await self._canvas_lifecycle.create_empty(int(episode.id))
        return assembly.episode_view(episode, cover=None)

    async def list_for_project(
        self,
        user_id: int,
        project_id: int,
        *,
        page: int,
        page_size: int,
        query_text: str,
    ) -> tuple[list[EpisodeView], int]:
        project = await self._projects.get_owned(user_id, project_id)
        if project is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        rows, total = await self._episodes.list_active(
            project_id,
            page=page,
            page_size=page_size,
            query_text=query_text,
        )
        return await self._views(rows, user_id=user_id), total

    async def get(self, user_id: int, episode_id: int) -> EpisodeView:
        scope = await self._scopes.require_read_scope(user_id, episode_id)
        episode = await self._episodes.get_active(scope.episode_id)
        if episode is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
        return (await self._views([episode], user_id=user_id))[0]

    async def update(
        self,
        user_id: int,
        episode_id: int,
        changes: NameAndCoverUpdate,
    ) -> EpisodeView:
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        normalized = await _normalize_update(changes, user_id=user_id, covers=self._covers)
        row = await self._episodes.update_name_and_cover(scope.episode_id, changes=normalized)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
        return (await self._views([row], user_id=user_id))[0]

    async def raise_if_canvas_busy(self, episode_id: int) -> None:
        """画布节点/任务在途则禁止删集, 零副作用"""
        canvas_state = await self._canvas_lifecycle.get_delete_state(episode_id)
        if canvas_state.running_node_count or await self._generation_tasks.has_non_terminal(
            canvas_state.task_ids
        ):
            raise AppError(ErrorCode.CANVAS_EPISODE_BUSY, "canvas episode is busy")

    async def delete(self, user_id: int, episode_id: int, *, require_idle: bool = True) -> None:
        """软删集; 调用方须已完成会话清理

        require_idle=True 时再检 busy; 删集用例已在持锁窗口入口检过则传 False, 避免半死后失败
        """
        async with in_transaction():
            episode_snapshot = await self._episodes.get_active(episode_id)
            if episode_snapshot is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            project_id = episode_snapshot.project_id
            project = await self._projects.get_owned_for_update(user_id, project_id)
            if project is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
            episode = await self._episodes.get_active_for_update(episode_id)
            if episode is None or episode.project_id != project_id:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            if await self._episodes.count_active(project_id) <= 1:
                raise AppError(ErrorCode.EPISODE_LAST_REMAINING, "project must keep at least one episode")

            if require_idle:
                await self.raise_if_canvas_busy(episode_id)

            await self._canvas_lifecycle.delete_all(episode_id)
            if not await self._episodes.soft_delete(episode_id):
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")


async def _normalize_update(
    changes: NameAndCoverUpdate,
    *,
    user_id: int,
    covers: CoverService,
) -> NameAndCoverUpdate:
    if not changes.name.provided and not changes.cover_asset_id.provided:
        raise AppError(ErrorCode.INVALID_PARAMS, "name or cover_asset_id required")

    name = changes.name
    if name.provided:
        clean_name = name.value.strip() if name.value is not None else ""
        if not clean_name:
            raise AppError(ErrorCode.INVALID_PARAMS, "name required")
        if len(clean_name) > 255:
            raise AppError(ErrorCode.INVALID_PARAMS, "name too long")
        name = UpdateField.set(clean_name)

    if changes.cover_asset_id.provided and changes.cover_asset_id.value is not None:
        await covers.require_cover_asset(user_id=user_id, asset_id=changes.cover_asset_id.value)

    return NameAndCoverUpdate(name=name, cover_asset_id=changes.cover_asset_id)


_project_repository = ProjectRepository()
_episode_repository = EpisodeRepository()
_canvas_lifecycle_repository = CanvasEpisodeLifecycleRepository()
_canvas_scope_service = CanvasScopeService(
    projects=_project_repository,
    episodes=_episode_repository,
)

project_service = ProjectService(
    projects=_project_repository,
    episodes=_episode_repository,
    canvas_lifecycle=_canvas_lifecycle_repository,
    covers=cover_service,
)
episode_service = EpisodeService(
    projects=_project_repository,
    episodes=_episode_repository,
    canvas_lifecycle=_canvas_lifecycle_repository,
    generation_tasks=GenerationTaskStateService(GenerateTaskRepository()),
    scopes=_canvas_scope_service,
    covers=cover_service,
)
canvas_scope_service = _canvas_scope_service
