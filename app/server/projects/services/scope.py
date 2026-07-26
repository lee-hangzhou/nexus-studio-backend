from __future__ import annotations

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.repositories import EpisodeRepository, ProjectRepository


class CanvasScopeService:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        episodes: EpisodeRepository,
    ) -> None:
        self._projects = projects
        self._episodes = episodes

    async def require_read_scope(self, user_id: int, episode_id: int) -> CanvasScope:
        episode = await self._episodes.get_active(episode_id)
        if episode is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
        project = await self._projects.get_owned(user_id, int(episode.project_id))
        if project is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "project not found")
        return CanvasScope(project_id=int(episode.project_id), episode_id=int(episode.id), user_id=user_id)

    async def require_write_scope(self, user_id: int, episode_id: int) -> CanvasScope:
        return await self.require_read_scope(user_id, episode_id)
