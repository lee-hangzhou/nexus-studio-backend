from fastapi import APIRouter, Request

from app.server.projects.schemas import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectIdRequest,
    ProjectListRequest,
    ProjectListResponse,
    ProjectUpdateRequest,
    ProjectView,
)
from app.server.projects.domain.models import NameAndCoverUpdate, UpdateField
from app.server.projects.services.service import project_service
from app.server.api.schemas import Response

router = APIRouter()


@router.post("/list")
async def list_projects(request: Request, body: ProjectListRequest) -> Response[ProjectListResponse]:
    user_id: int = request.state.user_id
    items, total = await project_service.list_for_user(
        user_id,
        page=body.page,
        page_size=body.page_size,
        query_text=body.query,
    )
    return Response(
        data=ProjectListResponse(
            items=items,
            page=body.page,
            page_size=body.page_size,
            total=total,
        )
    )


@router.post("/create")
async def create_project(request: Request, body: ProjectCreateRequest) -> Response[ProjectCreateResponse]:
    user_id: int = request.state.user_id
    created = await project_service.create(user_id, body.name)
    return Response(data=created)


@router.post("/get")
async def get_project(request: Request, body: ProjectIdRequest) -> Response[ProjectDetailResponse]:
    user_id: int = request.state.user_id
    detail = await project_service.get_for_user(user_id, body.project_id)
    return Response(data=detail)


@router.post("/update")
async def update_project(request: Request, body: ProjectUpdateRequest) -> Response[ProjectView]:
    user_id: int = request.state.user_id
    project = await project_service.update(
        user_id,
        body.project_id,
        NameAndCoverUpdate(
            name=UpdateField.set(body.name) if "name" in body.model_fields_set else UpdateField.omitted(),
            cover_asset_id=(
                UpdateField.set(body.cover_asset_id)
                if "cover_asset_id" in body.model_fields_set
                else UpdateField.omitted()
            ),
        ),
    )
    return Response(data=project)
