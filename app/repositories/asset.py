from collections.abc import Collection

from app.models.assets import Assets
from app.repositories.base import BaseRepository


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
