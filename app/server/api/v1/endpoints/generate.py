from fastapi import APIRouter, File, Request, UploadFile

from app.agent.canvas.services.generation_projection import project_from_task
from app.composition import generation_service
from app.server.api.schemas import Response
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.schemas import (
    GenerateCallbackPayload,
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTaskListRequest,
    GenerateTaskListResponse,
    GenerateTasksStatusRequest,
    GenerateTasksStatusResponse,
    GenerateTaskStatusRequest,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    SubmitGenerateRequest,
    TaskCancelRequest,
    TaskDeleteRequest,
    TaskFavoriteRequest,
)

router = APIRouter()


@router.post("/submit")
async def submit_generate(
    request: Request,
    body: SubmitGenerateRequest,
) -> Response[GenerateTaskSubmitResponse]:
    user_id: int = request.state.user_id
    result = await generation_service.submit(user_id, body)
    return Response(data=result)


@router.post("/material/upload")
async def upload_generate_material(
    request: Request,
    file: UploadFile = File(...),
) -> Response[GenerateMaterialUploadResponse]:
    user_id: int = request.state.user_id
    await file.seek(0)
    raw = await file.read()
    result = await generation_service.upload_material(
        user_id,
        filename=file.filename or "",
        mime_type=file.content_type or "",
        raw_bytes=raw,
    )
    return Response(data=result)


@router.post("/task/status")
async def get_task_status(
    request: Request,
    body: GenerateTaskStatusRequest,
) -> Response[GenerateTaskView]:
    user_id: int = request.state.user_id
    result = await generation_service.get_task_status(body.task_id, user_id)
    return Response(data=result)


@router.post("/tasks/status")
async def get_tasks_status(
    request: Request,
    body: GenerateTasksStatusRequest,
) -> Response[GenerateTasksStatusResponse]:
    result = await generation_service.get_tasks_status(
        body.task_ids,
        request.state.user_id,
    )
    return Response(data=result)


@router.post("/tasks/list")
async def list_tasks(
    request: Request,
    body: GenerateTaskListRequest,
) -> Response[GenerateTaskListResponse]:
    user_id: int = request.state.user_id
    result = await generation_service.list_tasks(user_id, body)
    return Response(data=result)


@router.post("/task/cancel")
async def cancel_task(request: Request, body: TaskCancelRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generation_service.cancel(body.task_id, user_id)
    return Response(data={"cancelled": True})


@router.post("/task/favorite")
async def favorite_task(request: Request, body: TaskFavoriteRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generation_service.toggle_favorite(
        body.task_id,
        user_id,
        body.favorited,
    )
    return Response(data={"favorited": body.favorited})


@router.post("/task/delete")
async def delete_task(request: Request, body: TaskDeleteRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generation_service.delete_task(body.task_id, user_id)
    return Response(data={"deleted": True})


@router.get("/models")
async def list_models(kind: str = "image") -> Response[GenerateModelsResponse]:
    result = await generation_service.list_models(GenerationKind(kind))
    return Response(data=result)


@router.get("/voices")
async def list_voices(model: str) -> Response[dict]:
    items = await generation_service.list_tts_voices(model)
    return Response(
        data={
            "object": "list",
            "model": model,
            "items": [item.model_dump(mode="json", by_alias=True) for item in items],
        }
    )


@router.post("/callback")
async def receive_generate_callback(payload: GenerateCallbackPayload) -> Response[dict]:
    """接收 union_lm 网关回调（内网专用，已在 JWT 白名单中放行）"""
    outcome = await generation_service.handle_callback(payload)
    await project_from_task(outcome.task)
    return Response(data={"accepted": True})
