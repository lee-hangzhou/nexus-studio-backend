from fastapi import APIRouter, Request

from app.server.api.use_cases import canvas_episode_use_cases
from app.server.api.schemas import Response
from app.server.projects.domain.models import NameAndCoverUpdate, UpdateField
from app.server.projects.schemas import (
    EpisodeCreateRequest,
    EpisodeIdRequest,
    EpisodeListRequest,
    EpisodeListResponse,
    EpisodeUpdateRequest,
    EpisodeView,
)
from app.server.projects.services.service import episode_service

router = APIRouter()


@router.post("/list")
async def list_episodes(request: Request, body: EpisodeListRequest) -> Response[EpisodeListResponse]:
    user_id: int = request.state.user_id
    items, total = await episode_service.list_for_project(
        user_id,
        body.project_id,
        page=body.page,
        page_size=body.page_size,
        query_text=body.query,
    )
    return Response(
        data=EpisodeListResponse(
            items=items,
            page=body.page,
            page_size=body.page_size,
            total=total,
        )
    )


@router.post("/create")
async def create_episode(request: Request, body: EpisodeCreateRequest) -> Response[EpisodeView]:
    user_id: int = request.state.user_id
    episode = await episode_service.create(user_id, body.project_id, body.name)
    return Response(data=episode)


@router.post("/get")
async def get_episode(request: Request, body: EpisodeIdRequest) -> Response[EpisodeView]:
    user_id: int = request.state.user_id
    episode = await episode_service.get(user_id, body.episode_id)
    return Response(data=episode)


@router.post("/update")
async def update_episode(request: Request, body: EpisodeUpdateRequest) -> Response[EpisodeView]:
    user_id: int = request.state.user_id
    episode = await episode_service.update(
        user_id,
        body.episode_id,
        NameAndCoverUpdate(
            name=UpdateField.set(body.name) if "name" in body.model_fields_set else UpdateField.omitted(),
            cover_asset_id=(
                UpdateField.set(body.cover_asset_id)
                if "cover_asset_id" in body.model_fields_set
                else UpdateField.omitted()
            ),
        ),
    )
    return Response(data=episode)


@router.post("/delete")
async def delete_episode(request: Request, body: EpisodeIdRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await canvas_episode_use_cases.delete_episode(user_id=user_id, episode_id=body.episode_id)
    return Response(data={})
