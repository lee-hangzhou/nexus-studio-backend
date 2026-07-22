from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from app.contracts.gateway import GatewayGenerateMaterial
from app.server.infra.object_storage import TosObjectStorage, safe_filename
from app.server.generation.domain.enums import MaterialType
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.assets.persistence.assets import Assets

ASSET_SOURCE_CHAT_UPLOAD = "chat_upload"
ASSET_SOURCE_GENERATE_RESULT = "generate_result"
ASSET_SOURCE_CANVAS_NODE_OUTPUT = "canvas_node_output"
ASSET_SOURCE_MANUAL_UPLOAD = "manual_upload"

ASSET_TYPE_IMAGE = "image"
ASSET_TYPE_VIDEO = "video"
ASSET_TYPE_AUDIO = "audio"
ASSET_TYPE_TEXT = "text"

def asset_type_from_mime(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return ASSET_TYPE_IMAGE
    if mime_type.startswith("video/"):
        return ASSET_TYPE_VIDEO
    if mime_type.startswith("audio/"):
        return ASSET_TYPE_AUDIO
    return ASSET_TYPE_TEXT


def material_type_from_asset_type(asset_type: str) -> MaterialType:
    if asset_type == ASSET_TYPE_IMAGE:
        return MaterialType.IMAGE
    if asset_type == ASSET_TYPE_VIDEO:
        return MaterialType.VIDEO
    if asset_type == ASSET_TYPE_AUDIO:
        return MaterialType.AUDIO
    raise AppError(ErrorCode.INVALID_PARAMS, "不支持将文本资产作为生成参考素材")


@dataclass(frozen=True)
class AssetView:
    id: int
    user_id: int
    project_id: int | None
    storage_key: str
    filename: str
    mime_type: str
    asset_type: str
    source_type: str
    source_id: str | None
    metadata: dict[str, Any]
    status: str
    favorite: bool
    preview_url: str


class AssetService:
    def __init__(self) -> None:
        self.storage = TosObjectStorage()

    async def create_asset(
        self,
        *,
        user_id: int,
        storage_key: str,
        mime_type: str,
        asset_type: str | None = None,
        filename: str = "",
        source_type: str,
        source_id: str | int | None = None,
        project_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "ready",
    ) -> Assets:
        source_id_text = str(source_id) if source_id is not None else None
        existing = None
        if source_id_text:
            existing = await Assets.filter(
                user_id=user_id,
                storage_key=storage_key,
                source_type=source_type,
                source_id=source_id_text,
                deleted_at__isnull=True,
            ).first()
        if existing is not None:
            return existing
        return await Assets.create(
            user_id=user_id,
            project_id=project_id,
            storage_key=storage_key,
            filename=filename,
            mime_type=mime_type,
            asset_type=asset_type or asset_type_from_mime(mime_type),
            source_type=source_type,
            source_id=source_id_text,
            metadata=metadata or {},
            status=status,
        )

    async def get_owned_assets(self, *, user_id: int, asset_ids: list[int]) -> list[Assets]:
        if not asset_ids:
            return []
        deduped = list(dict.fromkeys(int(item) for item in asset_ids))
        return await Assets.filter(user_id=user_id, id__in=deduped, deleted_at__isnull=True).all()

    async def require_owned(self, *, user_id: int, asset_id: int) -> Assets:
        row = await Assets.filter(user_id=user_id, id=asset_id, deleted_at__isnull=True).first()
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "asset not found")
        return row

    async def upload_manual_asset(self, *, user_id: int, file: UploadFile) -> Assets:
        filename = safe_filename(file.filename or "upload.bin")
        mime_type = file.content_type or "application/octet-stream"
        storage_key = f"assets/{user_id}/{uuid4().hex}/{filename}"
        await self.storage.put_upload_file(storage_key, file, content_type=mime_type)
        return await self.create_asset(
            user_id=user_id,
            storage_key=storage_key,
            filename=filename,
            mime_type=mime_type,
            asset_type=asset_type_from_mime(mime_type),
            source_type=ASSET_SOURCE_MANUAL_UPLOAD,
            metadata={"filename": filename},
        )

    async def update_asset(
        self,
        *,
        user_id: int,
        asset_id: int,
        filename: str | None = None,
        favorite: bool | None = None,
    ) -> Assets:
        row = await self.require_owned(user_id=user_id, asset_id=asset_id)
        update_fields: list[str] = []
        if filename is not None:
            row.filename = safe_filename(filename)
            update_fields.append("filename")
        if favorite is not None:
            row.favorite = favorite
            update_fields.append("favorite")
        if update_fields:
            await row.save(update_fields=update_fields)
        return row

    async def soft_delete_assets(self, *, user_id: int, asset_ids: list[int]) -> int:
        deduped = list(dict.fromkeys(int(item) for item in asset_ids))
        if not deduped:
            return 0
        return await Assets.filter(
            user_id=user_id,
            id__in=deduped,
            deleted_at__isnull=True,
        ).update(deleted_at=datetime.now(timezone.utc))

    def preview_url(self, storage_key: str) -> str:
        return self.storage.presigned_get_url(storage_key)

    def to_view(self, row: Assets) -> AssetView:
        return AssetView(
            id=int(row.id),
            user_id=int(row.user_id),
            project_id=int(row.project_id) if row.project_id is not None else None,
            storage_key=row.storage_key,
            filename=row.filename,
            mime_type=row.mime_type,
            asset_type=row.asset_type,
            source_type=row.source_type,
            source_id=row.source_id,
            metadata=row.metadata or {},
            status=row.status,
            favorite=bool(row.favorite),
            preview_url=self.preview_url(row.storage_key),
        )

    async def assets_to_gateway_materials(
        self,
        *,
        user_id: int,
        asset_ids: list[int],
    ) -> list[GatewayGenerateMaterial]:
        rows = await self.get_owned_assets(user_id=user_id, asset_ids=asset_ids)
        by_id = {int(row.id): row for row in rows}
        missing = [int(item) for item in dict.fromkeys(asset_ids) if int(item) not in by_id]
        if missing:
            raise AppError(ErrorCode.INVALID_PARAMS, "引用资产不存在或无权访问", {"asset_ids": missing})
        materials: list[GatewayGenerateMaterial] = []
        for asset_id in dict.fromkeys(asset_ids):
            row = by_id[int(asset_id)]
            materials.append(
                GatewayGenerateMaterial(
                    type=material_type_from_asset_type(row.asset_type),
                    storage_key=row.storage_key,
                )
            )
        return materials


asset_service = AssetService()
