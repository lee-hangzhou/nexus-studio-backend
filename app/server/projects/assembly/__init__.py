from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects
from app.server.projects.persistence.repositories import ProjectActivityRow
from app.server.projects.schemas import CoverView, EpisodeView, ProjectView


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def project_view(
    row: Projects,
    *,
    cover: CoverView | None,
    episode_count: int,
    activity_at: datetime,
) -> ProjectView:
    cover_asset_id = int(row.cover_asset_id) if row.cover_asset_id is not None else None
    return ProjectView(
        id=int(row.id),
        name=row.name,
        status=int(row.status),
        episode_count=episode_count,
        activity_at=_iso(activity_at),
        cover_asset_id=cover_asset_id,
        cover=cover,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def project_activity_views(
    rows: Sequence[ProjectActivityRow],
    *,
    covers_by_id: Mapping[int, CoverView],
) -> list[ProjectView]:
    return [
        project_view(
            row.project,
            cover=(
                covers_by_id.get(int(row.project.cover_asset_id))
                if row.project.cover_asset_id is not None
                else None
            ),
            episode_count=row.episode_count,
            activity_at=row.activity_at,
        )
        for row in rows
    ]


def episode_view(row: ProjectEpisodes, *, cover: CoverView | None) -> EpisodeView:
    cover_asset_id = int(row.cover_asset_id) if row.cover_asset_id is not None else None
    return EpisodeView(
        id=int(row.id),
        project_id=int(row.project_id),
        creator_id=int(row.creator_id),
        episode_no=int(row.episode_no),
        name=row.name,
        cover_asset_id=cover_asset_id,
        cover=cover,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def episode_views(
    rows: Sequence[ProjectEpisodes],
    *,
    covers_by_id: Mapping[int, CoverView],
) -> list[EpisodeView]:
    return [
        episode_view(
            row,
            cover=(covers_by_id.get(int(row.cover_asset_id)) if row.cover_asset_id is not None else None),
        )
        for row in rows
    ]
