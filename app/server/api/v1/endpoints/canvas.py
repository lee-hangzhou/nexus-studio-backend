from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.runtime.stream.replay import (
    ReplayMeta,
    replay_store,
    stream_replay,
    validate_replay_cursor,
)
from app.server.api.schemas import Response
from app.server.api.use_cases import canvas_episode_use_cases
from app.server.canvas.domain.constants import CANVAS_TURN_LOCK_KEY_TEMPLATE
from app.server.canvas.schemas.api import (
    CanvasMessagesListRequest,
    CanvasMessageView,
    CanvasNodeGenerateResponse,
    CanvasPatchRequest,
    CanvasReconnectRequest,
    CanvasResumeRequest,
    CanvasSnapshot,
    CanvasTurnRequest,
)
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.server.canvas.services.canvas_service import CanvasRevisionConflictError, canvas_service
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.services.service import canvas_scope_service

router = APIRouter()


def _canvas_turn_lock_key(episode_id: int) -> str:
    return CANVAS_TURN_LOCK_KEY_TEMPLATE.format(episode_id=episode_id)


def _stream_response(meta: ReplayMeta, request: Request) -> StreamingResponse:
    last_event_id = request.headers.get("Last-Event-ID")
    validate_replay_cursor(meta, last_event_id)

    async def gen() -> AsyncIterator[str]:
        async for chunk in stream_replay(
            replay_store,
            meta,
            last_event_id=last_event_id,
            execution_lock_key=_canvas_turn_lock_key(meta.session_id),
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
    scope = await canvas_scope_service.require_read_scope(request.state.user_id, episode_id)
    snapshot = await canvas_service.get_snapshot(scope)
    return Response(data=snapshot)


@router.post("/episodes/{episode_id}/patch")
async def patch_canvas(episode_id: int, request: Request, body: CanvasPatchRequest) -> Response:
    scope = await canvas_scope_service.require_write_scope(request.state.user_id, episode_id)
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


@router.post("/episodes/{episode_id}/messages/list")
async def list_canvas_messages(
    episode_id: int,
    request: Request,
    body: CanvasMessagesListRequest,
) -> Response[list[CanvasMessageView]]:
    scope = await canvas_scope_service.require_read_scope(request.state.user_id, episode_id)
    items = await canvas_service.list_messages(
        scope,
        before_id=body.before_id,
        limit=body.limit,
    )
    return Response(data=items)


@router.post("/episodes/{episode_id}/turn")
async def canvas_turn_stream(episode_id: int, request: Request, body: CanvasTurnRequest) -> StreamingResponse:
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
    user_id: int = request.state.user_id
    meta = await canvas_episode_use_cases.reconnect_turn(
        user_id=user_id,
        episode_id=episode_id,
        request_id=str(body.request_id),
        last_event_id=request.headers.get("Last-Event-ID"),
    )
    return _stream_response(meta, request)


@router.post("/episodes/{episode_id}/turn/cancel")
async def canvas_turn_cancel(episode_id: int, request: Request) -> Response[dict]:
    result = await canvas_episode_use_cases.cancel_turn(
        user_id=request.state.user_id,
        episode_id=episode_id,
    )
    return Response(data={"cancelled": result.cancelled, "active_turn_id": result.active_turn_id})


@router.post("/episodes/{episode_id}/nodes/{node_id}/generate")
async def canvas_node_generate(
    episode_id: int,
    node_id: str,
    request: Request,
    body: SubmitNodeExecuteInput,
) -> Response[CanvasNodeGenerateResponse]:
    user_id: int = request.state.user_id
    result = await canvas_episode_use_cases.generate_node(
        user_id=user_id,
        episode_id=episode_id,
        node_id=node_id,
        body=body,
    )
    return Response(data=result)
