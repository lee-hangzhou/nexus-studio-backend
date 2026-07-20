"""创作页生成任务 API 端点

公开路由（受 JWT 中间件保护）：
  POST /generate/submit           提交生成任务
  POST /generate/task/status      查询单任务状态
  POST /generate/tasks/status     批量查询任务状态
  POST /generate/history          历史列表（cursor 分页）
  POST /generate/task/cancel      取消任务
  POST /generate/task/favorite    收藏/取消收藏
  POST /generate/task/delete      删除历史记录
  GET  /generate/models           模型列表

内网回调（在 JWT 中间件白名单中放行，仅 Docker 内网可达）：
  POST /generate/callback         接收 union_lm 网关回调
"""

from fastapi import APIRouter, File, Request, UploadFile

from app.schemas.base import Response
from app.schemas.generate import (
    GenerateCallbackPayload,
    GenerateMaterialUploadResponse,
    GenerateModelsResponse,
    GenerateTasksStatusRequest,
    GenerateTasksStatusResponse,
    GenerateTaskStatusRequest,
    GenerateTaskSubmitResponse,
    GenerateTaskView,
    HistoryRequest,
    HistoryResponse,
    SubmitGenerateRequest,
    TaskCancelRequest,
    TaskDeleteRequest,
    TaskFavoriteRequest,
)
from app.services.generate import generate_service

router = APIRouter()


@router.post("/submit")
async def submit_generate(request: Request, body: SubmitGenerateRequest) -> Response[GenerateTaskSubmitResponse]:
    user_id: int = request.state.user_id
    result = await generate_service.submit(user_id, body)
    return Response(data=result)


@router.post("/material/upload")
async def upload_generate_material(
    request: Request,
    file: UploadFile = File(...),
) -> Response[GenerateMaterialUploadResponse]:
    user_id: int = request.state.user_id
    result = await generate_service.upload_material(user_id, file)
    return Response(data=result)


@router.post("/task/status")
async def get_task_status(request: Request, body: GenerateTaskStatusRequest) -> Response[GenerateTaskView]:
    user_id: int = request.state.user_id
    result = await generate_service.get_task_status(body.task_id, user_id)
    return Response(data=result)


@router.post("/tasks/status")
async def get_tasks_status(
    request: Request,
    body: GenerateTasksStatusRequest,
) -> Response[GenerateTasksStatusResponse]:
    user_id: int = request.state.user_id
    result = await generate_service.get_tasks_status(body.task_ids, user_id)
    return Response(data=result)


@router.post("/history")
async def list_history(request: Request, body: HistoryRequest) -> Response[HistoryResponse]:
    user_id: int = request.state.user_id
    result = await generate_service.list_history(user_id, body)
    return Response(data=result)


@router.post("/task/cancel")
async def cancel_task(request: Request, body: TaskCancelRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generate_service.cancel(body.task_id, user_id)
    return Response(data={"cancelled": True})


@router.post("/task/favorite")
async def favorite_task(request: Request, body: TaskFavoriteRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generate_service.toggle_favorite(body.task_id, user_id, body.favorited)
    return Response(data={"favorited": body.favorited})


@router.post("/task/delete")
async def delete_task(request: Request, body: TaskDeleteRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await generate_service.delete_task(body.task_id, user_id)
    return Response(data={"deleted": True})


@router.get("/models")
async def list_models(kind: str = "image") -> Response[GenerateModelsResponse]:
    result = await generate_service.list_models(kind)
    return Response(data=result)


@router.get("/voices")
async def list_voices(model: str) -> Response[dict]:
    from app.services.generation_voices import list_tts_voices

    items = await list_tts_voices(model)
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
    await generate_service.handle_callback(payload)
    return Response(data={"accepted": True})
