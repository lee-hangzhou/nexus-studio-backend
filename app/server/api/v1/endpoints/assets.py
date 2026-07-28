from datetime import datetime

from fastapi import APIRouter, File, Request, UploadFile
from tortoise.transactions import in_transaction

from app.server.assets.schemas import (
    AssetDeleteRequest,
    AssetIdRequest,
    AssetListRequest,
    AssetListResponse,
    AssetUpdateRequest,
    AssetViewResponse,
)
from app.server.assets.services.service import LIBRARY_SOURCE_TYPES, asset_service
from app.server.assets.persistence.assets import Assets
from app.server.api.schemas import Response
from app.server.projects.services.cover import cover_service

router = APIRouter()


def _asset_response(row: Assets) -> AssetViewResponse:
    view = asset_service.to_view(row)
    return AssetViewResponse(
        id=view.id,
        project_id=view.project_id,
        filename=view.filename,
        mime_type=view.mime_type,
        asset_type=view.asset_type,
        source_type=view.source_type,
        source_id=view.source_id,
        metadata=view.metadata,
        status=view.status,
        favorite=view.favorite,
        preview_url=view.preview_url,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.post("/list")
async def list_assets(request: Request, body: AssetListRequest) -> Response[AssetListResponse]:
    user_id: int = request.state.user_id
    query = Assets.filter(user_id=user_id, deleted_at__isnull=True)
    keyword = body.query.strip()
    if keyword:
        query = query.filter(filename__icontains=keyword)
    if body.asset_type != "all":
        query = query.filter(asset_type=body.asset_type)
    if body.source_type == "all":
        pass
    elif body.source_type == "library":
        query = query.filter(source_type__in=list(LIBRARY_SOURCE_TYPES))
    else:
        query = query.filter(source_type=body.source_type)
    if body.favorites_only:
        query = query.filter(favorite=True)
    created_from = _parse_iso_datetime(body.created_from)
    created_to = _parse_iso_datetime(body.created_to)
    if created_from is not None:
        query = query.filter(created_at__gte=created_from)
    if created_to is not None:
        query = query.filter(created_at__lte=created_to)

    total = await query.count()
    rows = (
        await query
        .order_by("-created_at", "-id")
        .offset((body.page - 1) * body.page_size)
        .limit(body.page_size)
    )
    return Response(
        data=AssetListResponse(
            items=[_asset_response(row) for row in rows],
            page=body.page,
            page_size=body.page_size,
            total=total,
        )
    )


@router.post("/get")
async def get_asset(request: Request, body: AssetIdRequest) -> Response[AssetViewResponse]:
    user_id: int = request.state.user_id
    row = await asset_service.require_owned(user_id=user_id, asset_id=body.asset_id)
    return Response(data=_asset_response(row))


@router.post("/upload")
async def upload_asset(request: Request, file: UploadFile = File(...)) -> Response[AssetViewResponse]:
    user_id: int = request.state.user_id
    row = await asset_service.upload_manual_asset(user_id=user_id, file=file)
    return Response(data=_asset_response(row))


@router.post("/update")
async def update_asset(request: Request, body: AssetUpdateRequest) -> Response[AssetViewResponse]:
    user_id: int = request.state.user_id
    row = await asset_service.update_asset(
        user_id=user_id,
        asset_id=body.asset_id,
        filename=body.filename,
        favorite=body.favorite,
    )
    return Response(data=_asset_response(row))


@router.post("/delete")
async def delete_assets(request: Request, body: AssetDeleteRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    async with in_transaction():
        deleted = await asset_service.soft_delete_assets(user_id=user_id, asset_ids=body.asset_ids)
        # Cover refs are owned by projects; clear them explicitly at the use-case boundary.
        await cover_service.clear_deleted_asset_refs(user_id=user_id, asset_ids=body.asset_ids)
    return Response(data={"deleted": deleted})
