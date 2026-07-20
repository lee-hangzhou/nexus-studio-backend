from fastapi import APIRouter, Request

from app.projects.schemas import (
    ProjectCreateRequest,
    ProjectIdRequest,
    ProjectListRequest,
    ProjectListResponse,
    ProjectView,
)
from app.projects.service import project_service
from app.schemas.base import Response

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
async def create_project(request: Request, body: ProjectCreateRequest) -> Response[ProjectView]:
    user_id: int = request.state.user_id
    project = await project_service.create(user_id, body.name)
    return Response(data=project)


@router.post("/get")
async def get_project(request: Request, body: ProjectIdRequest) -> Response[ProjectView]:
    user_id: int = request.state.user_id
    project = await project_service.get_for_user(user_id, body.project_id)
    return Response(data=project)
