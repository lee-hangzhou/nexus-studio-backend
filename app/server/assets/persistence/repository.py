from collections.abc import Collection
from typing import Mapping

from app.server.assets.persistence.assets import Assets
from app.server.persistence.repository_base import BaseRepository


class AssetRepository(BaseRepository[Assets]):
    def __init__(self) -> None:
        self.model = Assets

    async def get_active_by_ids_for_user(
        self,
        asset_ids: Collection[int],
        user_id: int,
    ) -> list[Assets]:
        if not asset_ids:
            return []
        return await self.model.filter(
            id__in=asset_ids,
            user_id=user_id,
            deleted_at__isnull=True,
        ).all()

    async def get_ready_visual_by_id_for_user(
        self,
        *,
        asset_id: int,
        user_id: int,
        visual_asset_types: Collection[str],
    ) -> Assets | None:
        return await self.model.filter(
            id=asset_id,
            user_id=user_id,
            asset_type__in=visual_asset_types,
            status="ready",
            deleted_at__isnull=True,
        ).first()

    async def get_ready_visual_by_ids_for_user(
        self,
        *,
        asset_ids: Collection[int],
        user_id: int,
        visual_asset_types: Collection[str],
        for_update: bool = False,
    ) -> list[Assets]:
        if not asset_ids:
            return []
        query = self.model.filter(
            id__in=asset_ids,
            user_id=user_id,
            asset_type__in=visual_asset_types,
            status="ready",
            deleted_at__isnull=True,
        )
        if for_update:
            query = query.select_for_update()
        return await query.all()

    async def update_generation_context(
        self,
        *,
        asset_id: int,
        project_id: int,
        current_project_id: int | None,
        metadata: Mapping[str, object],
    ) -> None:
        updates: dict[str, object] = {"metadata": dict(metadata)}
        if current_project_id is None:
            updates["project_id"] = project_id
        await self.model.filter(id=asset_id, deleted_at__isnull=True).update(**updates)

    async def get_favorited_by_ids_for_user(
        self,
        asset_ids: Collection[int],
        user_id: int,
    ) -> list[Assets]:
        if not asset_ids:
            return []
        return await self.model.filter(
            id__in=asset_ids,
            user_id=user_id,
            favorite=True,
            deleted_at__isnull=True,
        ).all()

    async def list_favorited_for_user(self, user_id: int) -> list[Assets]:
        return await self.model.filter(
            user_id=user_id,
            favorite=True,
            deleted_at__isnull=True,
        ).all()

    async def set_favorite_for_user(
        self,
        user_id: int,
        asset_ids: Collection[int],
        *,
        favorited: bool,
    ) -> None:
        if not asset_ids:
            return
        await self.model.filter(
            user_id=user_id,
            id__in=asset_ids,
            deleted_at__isnull=True,
        ).update(favorite=favorited)
