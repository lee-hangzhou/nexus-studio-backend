from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tortoise import connections
from tortoise.functions import Max

from app.server.projects.domain.enums import ProjectStatus
from app.server.projects.domain.models import CanvasScope, NameAndCoverUpdate
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects


@dataclass(frozen=True)
class ProjectActivityRow:
    project: Projects
    episode_count: int
    activity_at: datetime


class ProjectRepository:
    async def get_owned(self, user_id: int, project_id: int) -> Projects | None:
        return await Projects.filter(id=project_id, owner_user_id=str(user_id)).first()

    async def get_owned_for_update(self, user_id: int, project_id: int) -> Projects | None:
        return await Projects.select_for_update().filter(
            id=project_id,
            owner_user_id=str(user_id),
        ).first()

    async def create(self, user_id: int, name: str) -> Projects:
        return await Projects.create(
            owner_user_id=str(user_id),
            name=name,
            status=int(ProjectStatus.ACTIVE),
            cover_asset_id=None,
            tone_constraint={},
            style_constraint={},
            config={},
        )

    async def update_name_and_cover(
        self,
        project_id: int,
        *,
        changes: NameAndCoverUpdate,
    ) -> Projects | None:
        updates: dict[str, object] = {}
        if changes.name.provided:
            updates["name"] = changes.name.value
        if changes.cover_asset_id.provided:
            updates["cover_asset_id"] = changes.cover_asset_id.value
        if updates:
            updated = await Projects.filter(id=project_id).update(**updates)
            if updated != 1:
                return None
        return await Projects.filter(id=project_id).first()

    async def list_owned_with_activity(
        self,
        user_id: int,
        *,
        page: int,
        page_size: int,
        query_text: str,
    ) -> tuple[list[ProjectActivityRow], int]:
        query = Projects.filter(owner_user_id=str(user_id))
        keyword = query_text.strip()
        if keyword:
            query = query.filter(name__icontains=keyword)
        total = await query.count()
        if total == 0:
            return [], total

        rows = await connections.get("default").execute_query_dict(
            """
            SELECT
              p.id AS project_id,
              COALESCE(e.episode_count, 0) AS episode_count,
              CASE
                WHEN e.max_updated_at IS NOT NULL
                  AND e.max_updated_at > COALESCE(p.updated_at, p.created_at, CURRENT_TIMESTAMP)
                THEN e.max_updated_at
                ELSE COALESCE(p.updated_at, p.created_at, CURRENT_TIMESTAMP)
              END AS activity_at
            FROM projects p
            LEFT JOIN (
              SELECT project_id, COUNT(*) AS episode_count, MAX(updated_at) AS max_updated_at
              FROM project_episodes
              WHERE deleted_at IS NULL
              GROUP BY project_id
            ) e ON e.project_id = p.id
            WHERE p.owner_user_id = $1
              AND ($2 = '' OR p.name ILIKE ('%%' || $2 || '%%'))
            ORDER BY activity_at DESC, p.id DESC
            LIMIT $3 OFFSET $4
            """,
            [str(user_id), keyword, page_size, (page - 1) * page_size],
        )
        project_ids = [int(row["project_id"]) for row in rows]
        projects = await Projects.filter(id__in=project_ids).all() if project_ids else []
        projects_by_id = {int(project.id): project for project in projects}
        result: list[ProjectActivityRow] = []
        for row in rows:
            project = projects_by_id.get(int(row["project_id"]))
            if project is None:
                continue
            activity_at = row.get("activity_at")
            episode_count = int(row.get("episode_count") or 0)
            project_updated = project.updated_at or project.created_at or datetime.now(timezone.utc)
            if not isinstance(activity_at, datetime):
                activity_at = project_updated
            result.append(
                ProjectActivityRow(
                    project=project,
                    episode_count=episode_count,
                    activity_at=activity_at,
                )
            )
        return result, total


class EpisodeRepository:
    async def get_active(self, episode_id: int) -> ProjectEpisodes | None:
        return await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).first()

    async def get_active_for_update(self, episode_id: int) -> ProjectEpisodes | None:
        return await ProjectEpisodes.select_for_update().filter(
            id=episode_id,
            deleted_at__isnull=True,
        ).first()

    async def list_active(
        self,
        project_id: int,
        *,
        page: int = 1,
        page_size: int = 100,
        query_text: str = "",
    ) -> tuple[list[ProjectEpisodes], int]:
        query = ProjectEpisodes.filter(project_id=project_id, deleted_at__isnull=True)
        keyword = query_text.strip()
        if keyword:
            query = query.filter(name__icontains=keyword)
        total = await query.count()
        rows = (
            await query
            .order_by("episode_no", "id")
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows, total

    async def count_active(self, project_id: int) -> int:
        return await ProjectEpisodes.filter(project_id=project_id, deleted_at__isnull=True).count()

    async def max_episode_no(self, project_id: int) -> int:
        rows = await ProjectEpisodes.filter(project_id=project_id).annotate(max_no=Max("episode_no")).values("max_no")
        if not rows:
            return 0
        return int(rows[0].get("max_no") or 0)

    async def create(
        self,
        *,
        project_id: int,
        creator_id: int,
        episode_no: int,
        name: str,
    ) -> ProjectEpisodes:
        return await ProjectEpisodes.create(
            project_id=project_id,
            creator_id=creator_id,
            episode_no=episode_no,
            name=name,
            cover_asset_id=None,
        )

    async def update_name_and_cover(
        self,
        episode_id: int,
        *,
        changes: NameAndCoverUpdate,
    ) -> ProjectEpisodes | None:
        updates: dict[str, object] = {}
        if changes.name.provided:
            updates["name"] = changes.name.value
        if changes.cover_asset_id.provided:
            updates["cover_asset_id"] = changes.cover_asset_id.value
        if updates:
            updated = await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).update(**updates)
            if updated != 1:
                return None
        return await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).first()

    async def soft_delete(self, episode_id: int) -> bool:
        updated = await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).update(
            deleted_at=datetime.now(timezone.utc)
        )
        return updated == 1

    async def touch(self, episode_id: int) -> None:
        await ProjectEpisodes.filter(id=episode_id, deleted_at__isnull=True).update(
            updated_at=datetime.now(timezone.utc)
        )

class ProjectCoverRepository:
    async def fill_missing(
        self,
        *,
        scope: CanvasScope,
        cover_asset_id: int,
    ) -> bool:
        episode_updated = await ProjectEpisodes.filter(
            id=scope.episode_id,
            project_id=scope.project_id,
            cover_asset_id__isnull=True,
            deleted_at__isnull=True,
        ).update(cover_asset_id=cover_asset_id)
        project_updated = await Projects.filter(
            id=scope.project_id,
            owner_user_id=str(scope.user_id),
            cover_asset_id__isnull=True,
        ).update(cover_asset_id=cover_asset_id)
        return episode_updated == 1 or project_updated == 1

    async def clear_asset_refs_for_user(self, *, user_id: int, asset_ids: list[int]) -> None:
        deduped = [int(item) for item in dict.fromkeys(asset_ids)]
        if not deduped:
            return
        await Projects.filter(owner_user_id=str(user_id), cover_asset_id__in=deduped).update(
            cover_asset_id=None
        )
        project_ids = [
            int(row.id)
            for row in await Projects.filter(owner_user_id=str(user_id)).only("id")
        ]
        if project_ids:
            await ProjectEpisodes.filter(
                project_id__in=project_ids,
                cover_asset_id__in=deduped,
            ).update(cover_asset_id=None)
