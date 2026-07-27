from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.stream.replay import (
    ReplayMeta,
    replay_store,
    stream_replay,
    validate_replay_cursor,
)
from app.server.api.schemas import Response
from app.server.api.use_cases import canvas_episode_use_cases
from app.server.canvas.domain.constants import CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE
from app.server.canvas.schemas.api import (
    CanvasCancelRequest,
    CanvasMessagesListRequest,
    CanvasMessageView,
    CanvasNodeGenerateResponse,
    CanvasPatchRequest,
    CanvasReconnectRequest,
    CanvasResumeRequest,
    CanvasSessionCreateRequest,
    CanvasSessionIdRequest,
    CanvasSessionUpdateRequest,
    CanvasSessionView,
    CanvasSnapshot,
    CanvasTurnRequest,
)
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.server.canvas.services.canvas_service import CanvasRevisionConflictError, canvas_service
from app.server.canvas.services.episode_events import iter_episode_events
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.canvas.services.session_service import canvas_session_service
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.services.service import canvas_scope_service

router = APIRouter()


def _canvas_session_turn_lock_key(session_id: int) -> str:
    """拼 session 级 turn 执行锁 key"""
    return CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE.format(session_id=session_id)


def _stream_response(meta: ReplayMeta, request: Request) -> StreamingResponse:
    """把 ReplayMeta 包成可断线续传的 SSE 响应"""
    last_event_id = request.headers.get("Last-Event-ID")
    validate_replay_cursor(meta, last_event_id)

    async def gen() -> AsyncIterator[str]:
        """按 replay cursor 吐帧"""
        async for chunk in stream_replay(
            replay_store,
            meta,
            last_event_id=last_event_id,
            execution_lock_key=_canvas_session_turn_lock_key(meta.session_id),
            execution_lock_owner=meta.turn_id,
        ):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Stream-Request-ID": meta.request_id,
            "X-Turn-ID": meta.turn_id,
        },
    )


@router.post("/episodes/{episode_id}/get")
async def get_canvas_snapshot(episode_id: int, request: Request) -> Response[CanvasSnapshot]:
    """读取集级画布快照"""
    scope = await canvas_scope_service.require_read_scope(request.state.user_id, episode_id)
    snapshot = await canvas_service.get_snapshot(scope)
    return Response(data=snapshot)


@router.post("/episodes/{episode_id}/patch")
async def patch_canvas(episode_id: int, request: Request, body: CanvasPatchRequest) -> Response:
    """应用画布增量 patch; 删集窗口内拒绝"""
    scope = await canvas_scope_service.require_write_scope(request.state.user_id, episode_id)
    await canvas_episode_fence.assert_writable(scope.episode_id)
    try:
        result = await canvas_service.apply_patch(
            scope,
            body.ops,
        )
        return Response(data=result.model_dump())
    except CanvasRevisionConflictError as exc:
        raise AppError(
            ErrorCode.CANVAS_REVISION_CONFLICT,
            "revision conflict",
            details={**exc.details, "error_type": "revision_conflict"},
        ) from exc


@router.post("/episodes/{episode_id}/events")
async def canvas_episode_events(episode_id: int, request: Request) -> StreamingResponse:
    """集级图增量 SSE; 断线后应立刻 snapshot get 对齐"""
    scope = await canvas_scope_service.require_read_scope(request.state.user_id, episode_id)
    stop = asyncio.Event()

    async def gen() -> AsyncIterator[str]:
        """转发 Redis 集级 patch/progress/title"""
        try:
            async for payload in iter_episode_events(scope.episode_id, stop=stop):
                if await request.is_disconnected():
                    stop.set()
                    break
                patch = payload.canvas_patch
                if patch is not None:
                    yield encode_sse_frame(
                        create_stream_frame(
                            type=StreamFrameType.CANVAS_PATCH,
                            data=patch.model_dump(mode="json"),
                        )
                    )
                progress = payload.generation_progress
                if progress is not None:
                    yield encode_sse_frame(
                        create_stream_frame(
                            type=StreamFrameType.GENERATION_PROGRESS,
                            data=progress.model_dump(mode="json"),
                        )
                    )
                session_title = payload.session_title
                if session_title is not None:
                    yield encode_sse_frame(
                        create_stream_frame(
                            type=StreamFrameType.CANVAS_SESSION_TITLE,
                            session_id=session_title.session_id,
                            title=session_title.title,
                            updated_at=session_title.updated_at or "",
                        )
                    )
        finally:
            stop.set()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/episodes/{episode_id}/sessions/list")
async def list_canvas_sessions(episode_id: int, request: Request) -> Response[list[CanvasSessionView]]:
    """列出当前用户在该集的 ACTIVE 会话"""
    items = await canvas_episode_use_cases.list_sessions(
        user_id=request.state.user_id,
        episode_id=episode_id,
    )
    return Response(data=items)


@router.post("/episodes/{episode_id}/sessions/ensure")
async def ensure_canvas_default_session(episode_id: int, request: Request) -> Response[CanvasSessionView]:
    """确保存在 is_default ACTIVE 会话"""
    item = await canvas_episode_use_cases.ensure_default_session(
        user_id=request.state.user_id,
        episode_id=episode_id,
    )
    return Response(data=item)


@router.post("/episodes/{episode_id}/sessions/create")
async def create_canvas_session(
    episode_id: int,
    request: Request,
    body: CanvasSessionCreateRequest,
) -> Response[CanvasSessionView]:
    """创建非默认会话; 删集窗口内可能返回 busy"""
    item = await canvas_episode_use_cases.create_session(
        user_id=request.state.user_id,
        episode_id=episode_id,
        body=body,
    )
    return Response(data=item)


@router.post("/episodes/{episode_id}/sessions/update")
async def update_canvas_session(
    episode_id: int,
    request: Request,
    body: CanvasSessionUpdateRequest,
) -> Response[CanvasSessionView]:
    """更新会话标题并广播 canvas_session_title"""
    item = await canvas_episode_use_cases.update_session(
        user_id=request.state.user_id,
        episode_id=episode_id,
        body=body,
    )
    return Response(data=item)


@router.post("/episodes/{episode_id}/sessions/delete")
async def delete_canvas_session(
    episode_id: int,
    request: Request,
    body: CanvasSessionIdRequest,
) -> Response[dict]:
    """软关闭会话; 不可删掉最后一个 ACTIVE"""
    await canvas_episode_use_cases.delete_session(
        user_id=request.state.user_id,
        episode_id=episode_id,
        session_id=body.session_id,
    )
    return Response(data={"deleted": True})


@router.post("/episodes/{episode_id}/messages/list")
async def list_canvas_messages(
    episode_id: int,
    request: Request,
    body: CanvasMessagesListRequest,
) -> Response[list[CanvasMessageView]]:
    """列出会话消息; CLOSED 会话只读历史"""
    scope = await canvas_scope_service.require_read_scope(request.state.user_id, episode_id)
    await canvas_session_service.require_owned_session(scope, body.session_id, allow_closed=True)
    items = await canvas_service.list_messages(
        scope,
        session_id=body.session_id,
        before_id=body.before_id,
        limit=body.limit,
    )
    return Response(data=items)


@router.post("/episodes/{episode_id}/turn")
async def canvas_turn_stream(episode_id: int, request: Request, body: CanvasTurnRequest) -> StreamingResponse:
    """启动画布 Agent turn SSE"""
    user_id: int = request.state.user_id
    meta = await canvas_episode_use_cases.start_turn(
        user_id=user_id,
        episode_id=episode_id,
        body=body,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
    return _stream_response(meta, request)


@router.post("/episodes/{episode_id}/turn/resume")
async def canvas_turn_resume(
    episode_id: int,
    request: Request,
    body: CanvasResumeRequest,
) -> StreamingResponse:
    """恢复工具门后的 turn SSE"""
    user_id: int = request.state.user_id
    meta = await canvas_episode_use_cases.resume_turn(
        user_id=user_id,
        episode_id=episode_id,
        body=body,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
    return _stream_response(meta, request)


@router.post("/episodes/{episode_id}/turn/reconnect")
async def canvas_turn_reconnect(
    episode_id: int,
    request: Request,
    body: CanvasReconnectRequest,
) -> StreamingResponse:
    """按 request_id 重挂在途 turn SSE"""
    user_id: int = request.state.user_id
    meta = await canvas_episode_use_cases.reconnect_turn(
        user_id=user_id,
        episode_id=episode_id,
        session_id=body.session_id,
        request_id=str(body.request_id),
        last_event_id=request.headers.get("Last-Event-ID"),
    )
    return _stream_response(meta, request)


@router.post("/episodes/{episode_id}/turn/cancel")
async def canvas_turn_cancel(
    episode_id: int,
    request: Request,
    body: CanvasCancelRequest,
) -> Response[dict]:
    """取消当前 session 在途 turn"""
    result = await canvas_episode_use_cases.cancel_turn(
        user_id=request.state.user_id,
        episode_id=episode_id,
        session_id=body.session_id,
    )
    return Response(data={"cancelled": result.cancelled, "active_turn_id": result.active_turn_id})


@router.post("/episodes/{episode_id}/nodes/{node_id}/generate")
async def canvas_node_generate(
    episode_id: int,
    node_id: str,
    request: Request,
    body: SubmitNodeExecuteInput,
) -> Response[CanvasNodeGenerateResponse]:
    """手动触发生成节点; 删集窗口内可能 busy"""
    user_id: int = request.state.user_id
    result = await canvas_episode_use_cases.generate_node(
        user_id=user_id,
        episode_id=episode_id,
        node_id=node_id,
        body=body,
    )
    return Response(data=result)
