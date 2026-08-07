from datetime import datetime

from fastapi import APIRouter, Request
from tortoise.transactions import in_transaction

from app.server.api.schemas import Response
from app.server.assets.persistence.assets import Assets
from app.server.assets.schemas import (
    AssetDeleteRequest,
    AssetIdRequest,
    AssetListRequest,
    AssetListResponse,
    AssetRegisterRequest,
    AssetUpdateRequest,
    AssetUploadUrlRequest,
    AssetUploadUrlResponse,
    AssetViewResponse,
)
from app.server.assets.services.service import LIBRARY_SOURCE_TYPES, asset_service
from app.server.assets.domain.upload_rules import SOURCE_AGENT_UPLOAD, DirectUploadSourceType
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.services.service import canvas_scope_service, project_service
from app.server.projects.services.cover import cover_service

router = APIRouter()


def _asset_response(row: Assets) -> AssetViewResponse:
    """组装资产 HTTP 视图"""
    if row.created_at is None or row.updated_at is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "资产缺少时间戳")
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
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """解析可选 ISO 时间过滤条件"""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _resolve_direct_upload_project_id(
    *,
    user_id: int,
    source_type: DirectUploadSourceType,
    project_id: int | None,
    episode_id: int | None,
) -> int | None:
    """解析直传 project_id，agent 强制写权限，episode 走画布 scope"""
    if episode_id is not None:
        scope = await canvas_scope_service.require_write_scope(user_id, episode_id)
        if project_id is not None and project_id != scope.project_id:
            raise AppError(ErrorCode.INVALID_PARAMS, "project_id 与 episode 不匹配")
        return scope.project_id
    if source_type == SOURCE_AGENT_UPLOAD:
        if project_id is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "agent_upload 必须提供 project_id 或 episode_id")
        await project_service.get_for_user(user_id, project_id)
        return project_id
    return project_id


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


@router.post("/upload-url")
async def create_asset_upload_url(
    request: Request,
    body: AssetUploadUrlRequest,
) -> Response[AssetUploadUrlResponse]:
    """签发资产车道 TOS 直传 PUT URL"""
    user_id: int = request.state.user_id
    resolved_project_id = await _resolve_direct_upload_project_id(
        user_id=user_id,
        source_type=body.source_type,
        project_id=body.project_id,
        episode_id=body.episode_id,
    )
    minted = asset_service.create_upload_url(
        user_id=user_id,
        filename=body.filename,
        source_type=body.source_type,
        project_id=resolved_project_id,
    )
    return Response(
        data=AssetUploadUrlResponse(
            storage_key=minted.storage_key,
            upload_url=minted.upload_url,
            expires_in=minted.expires_in,
            method="PUT",
            source_type=minted.source_type,
        )
    )


@router.post("/register")
async def register_uploaded_asset(
    request: Request,
    body: AssetRegisterRequest,
) -> Response[AssetViewResponse]:
    """登记已直传对象为资产"""
    user_id: int = request.state.user_id
    resolved_project_id = await _resolve_direct_upload_project_id(
        user_id=user_id,
        source_type=body.source_type,
        project_id=body.project_id,
        episode_id=body.episode_id,
    )
    row = await asset_service.register_uploaded(
        user_id=user_id,
        storage_key=body.storage_key,
        filename=body.filename,
        mime_type=body.mime_type,
        source_type=body.source_type,
        project_id=resolved_project_id,
        size_bytes=body.size_bytes,
    )
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
