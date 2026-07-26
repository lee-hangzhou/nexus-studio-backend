from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from tortoise.transactions import in_transaction

from app.server.assets.persistence.repository import AssetRepository
from app.server.assets.services.service import ASSET_TYPE_IMAGE, ASSET_TYPE_VIDEO, asset_service
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.repositories import ProjectCoverRepository
from app.server.projects.schemas import CoverView

VISUAL_ASSET_TYPES = frozenset({ASSET_TYPE_IMAGE, ASSET_TYPE_VIDEO})


@dataclass(frozen=True, slots=True)
class CoverAsset:
    id: int
    asset_type: str
    storage_key: str


class CoverService:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        project_covers: ProjectCoverRepository,
        preview_url: Callable[[str], str],
    ) -> None:
        self._assets = assets
        self._project_covers = project_covers
        self._preview_url = preview_url

    async def require_cover_asset(self, *, user_id: int, asset_id: int) -> CoverAsset:
        row = await self._assets.get_ready_visual_by_id_for_user(
            asset_id=asset_id,
            user_id=user_id,
            visual_asset_types=VISUAL_ASSET_TYPES,
        )
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "cover asset not found")
        return CoverAsset(id=int(row.id), asset_type=row.asset_type, storage_key=row.storage_key)

    async def cover_view(self, asset_id: int | None, *, user_id: int | None = None) -> CoverView | None:
        views = await self.cover_views_by_id([asset_id], user_id=user_id)
        return views.get(asset_id) if asset_id is not None else None

    async def cover_views_by_id(
        self,
        asset_ids: Iterable[int | None],
        *,
        user_id: int | None = None,
    ) -> dict[int, CoverView]:
        deduped = [int(item) for item in dict.fromkeys(asset_ids) if item is not None]
        if not deduped:
            return {}
        if user_id is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "cover asset user scope required")
        rows = await self._assets.get_ready_visual_by_ids_for_user(
            asset_ids=deduped,
            user_id=user_id,
            visual_asset_types=VISUAL_ASSET_TYPES,
        )
        return {
            int(row.id): CoverView(
                asset_id=int(row.id),
                asset_type=row.asset_type,
                url=self._preview_url(row.storage_key),
            )
            for row in rows
        }

    async def fill_missing_cover_from_generation(
        self,
        scope: CanvasScope,
        *,
        node_id: str,
        generation_task_id: int | None,
        asset_ids: list[int] | None,
    ) -> int | None:
        if not asset_ids:
            return None
        ordered_asset_ids = [int(item) for item in asset_ids]
        async with in_transaction():
            rows = await self._assets.get_ready_visual_by_ids_for_user(
                asset_ids=list(dict.fromkeys(ordered_asset_ids)),
                user_id=scope.user_id,
                visual_asset_types=VISUAL_ASSET_TYPES,
                for_update=True,
            )
            by_id = {int(row.id): row for row in rows}
            candidate = next(
                (
                    row
                    for asset_id in ordered_asset_ids
                    if (row := by_id.get(asset_id)) is not None
                    and (row.project_id is None or int(row.project_id) == scope.project_id)
                ),
                None,
            )
            if candidate is None:
                return None

            metadata = dict(candidate.metadata or {})
            metadata.setdefault("project_id", scope.project_id)
            metadata.setdefault("episode_id", scope.episode_id)
            metadata.setdefault("node_id", node_id)
            if generation_task_id is not None:
                metadata.setdefault("generation_task_id", generation_task_id)
            await self._assets.update_generation_context(
                asset_id=int(candidate.id),
                project_id=scope.project_id,
                current_project_id=(int(candidate.project_id) if candidate.project_id is not None else None),
                metadata=metadata,
            )
            changed = await self._project_covers.fill_missing(
                scope=scope,
                cover_asset_id=int(candidate.id),
            )
            return int(candidate.id) if changed else None

    async def clear_deleted_asset_refs(self, *, user_id: int, asset_ids: list[int]) -> None:
        await self._project_covers.clear_asset_refs_for_user(user_id=user_id, asset_ids=asset_ids)


cover_service = CoverService(
    assets=AssetRepository(),
    project_covers=ProjectCoverRepository(),
    preview_url=asset_service.preview_url,
)
