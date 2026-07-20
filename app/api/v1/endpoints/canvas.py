import asyncio
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.canvas.node_execution.manual_generate import run_manual_node_generate
from app.canvas.schemas.api import (
    CanvasMessagesListRequest,
    CanvasMessageView,
    CanvasNodeGenerateResponse,
    CanvasPatchRequest,
    CanvasResumeRequest,
    CanvasSnapshot,
    CanvasTurnRequest,
)
from app.canvas.services.canvas_service import CanvasRevisionConflictError, canvas_service
from app.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.canvas.turn.lock import project_turn_lock
from app.canvas.turn.orchestrator import stream_canvas_resume, stream_canvas_turn
from app.canvas.turn.persistence import canvas_turn_already_completed
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.canvas_messages import CanvasMessages
from app.projects.service import project_service
from app.schemas.base import Response

router = APIRouter()


@router.get("/{project_id}")
async def get_canvas_snapshot(project_id: int, request: Request) -> Response[CanvasSnapshot]:
    """读取画布快照, 前端 patch 须基于返回的 revision"""
    await project_service.require_owned(request.state.user_id, project_id)
    snapshot = await canvas_service.get_snapshot(project_id)
    return Response(data=snapshot)


@router.patch("/{project_id}")
async def patch_canvas(project_id: int, request: Request, body: CanvasPatchRequest) -> Response:
    """前端直接提交画布补丁, 与 Agent 工具共用服务层"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    try:
        result = await canvas_service.apply_patch(
            project_id,
            body.ops,
            body.expected_revision,
            user_id=user_id,
        )
        return Response(data=result.model_dump())
    except CanvasRevisionConflictError as exc:
        raise AppError(
            ErrorCode.CANVAS_REVISION_CONFLICT,
            "revision conflict",
            details={**exc.details, "error_type": "revision_conflict"},
        ) from exc


@router.post("/{project_id}/messages/list")
async def list_canvas_messages(
    project_id: int,
    request: Request,
    body: CanvasMessagesListRequest,
) -> Response[list[CanvasMessageView]]:
    """分页读取画布 Agent 会话历史"""
    await project_service.require_owned(request.state.user_id, project_id)
    q = CanvasMessages.filter(project_id=project_id).order_by("-created_at")
    if body.before_id is not None:
        q = q.filter(id__lt=body.before_id)
    rows = await q.limit(body.limit)
    items = [
        CanvasMessageView(
            id=row.id,
            role=row.role,
            content=row.content,
            metadata=row.metadata or {},
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in reversed(rows)
    ]
    return Response(data=items)


@router.post("/{project_id}/turn")
async def canvas_turn_stream(project_id: int, request: Request, body: CanvasTurnRequest) -> StreamingResponse:
    """启动一轮 Canvas Agent 对话, SSE 持续返回事件"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    if body.client_turn_id and await canvas_turn_already_completed(project_id, body.client_turn_id):
        # client_turn_id 防前端重试导致同一轮输入重复执行
        raise AppError(
            ErrorCode.CANVAS_DUPLICATE_TURN,
            "canvas turn already completed",
            details={"client_turn_id": body.client_turn_id},
        )

    turn_id = uuid4().hex
    await project_turn_lock.acquire(project_id, turn_id)
    cancel_event = asyncio.Event()

    async def gen():
        """包装 orchestrator SSE 流, 感知浏览器断开"""
        try:
            async for chunk in stream_canvas_turn(
                project_id=project_id,
                user_id=user_id,
                content=body.content,
                model_key=body.model_key or "",
                client_turn_id=body.client_turn_id,
                mode=body.mode,
                enable_tools=body.enable_tools,
                cancel_event=cancel_event,
                turn_id=turn_id,
                lock_held=True,
            ):
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                yield chunk
        finally:
            await project_turn_lock.release(project_id, turn_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{project_id}/turn/resume")
async def canvas_turn_resume(
    project_id: int,
    request: Request,
    body: CanvasResumeRequest,
) -> StreamingResponse:
    """恢复手动模式下等待确认的工具调用"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    turn_id = body.client_turn_id or uuid4().hex
    await project_turn_lock.acquire(project_id, turn_id)
    cancel_event = asyncio.Event()

    async def gen():
        """继续输出恢复后的 Agent 事件流"""
        try:
            async for chunk in stream_canvas_resume(
                project_id=project_id,
                user_id=user_id,
                turn_id=turn_id,
                tool_call_id=body.tool_call_id,
                action=body.action,
                cancel_event=cancel_event,
                lock_held=True,
            ):
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                yield chunk
        finally:
            await project_turn_lock.release(project_id, turn_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{project_id}/turn/cancel")
async def canvas_turn_cancel(project_id: int, request: Request) -> Response[dict]:
    """取消当前项目正在运行的 Agent turn"""
    await project_service.require_owned(request.state.user_id, project_id)
    active = await project_turn_lock.force_cancel(project_id)
    return Response(data={"cancelled": active is not None, "active_turn_id": active})


@router.post("/{project_id}/nodes/{node_id}/generate")
async def canvas_node_generate(
    project_id: int,
    node_id: str,
    request: Request,
    body: SubmitNodeExecuteInput,
) -> Response[CanvasNodeGenerateResponse]:
    """用户手动提交节点生成；媒体 submit 后返回，终态靠轮询/callback。"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    body = body.model_copy(update={"node_id": node_id, "expected_revision": None})
    result = await run_manual_node_generate(
        project_id=project_id,
        user_id=user_id,
        body=body,
    )
    return Response(data=result)
