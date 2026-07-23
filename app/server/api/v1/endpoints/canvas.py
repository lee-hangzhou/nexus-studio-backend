import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.canvas.node_execution.manual_generate import run_manual_node_generate
from app.agent.canvas.turn.lock import project_turn_lock
from app.agent.canvas.turn.orchestrator import stream_canvas_resume, stream_canvas_turn
from app.agent.canvas.turn.persistence import canvas_turn_already_completed
from app.agent.canvas.turn.replay_execution import run_canvas_replay_execution
from app.agent.runtime.stream.replay import (
    ReplayMeta,
    ReplayRequestKind,
    build_request_fingerprint,
    execution_supervisor,
    replay_store,
    stream_replay,
    validate_replay_cursor,
)
from app.server.api.schemas import Response
from app.server.canvas.domain.constants import CANVAS_TURN_LOCK_KEY_TEMPLATE
from app.server.canvas.persistence.messages import CanvasMessages
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
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.projects.services.service import project_service

router = APIRouter()


def _canvas_turn_lock_key(project_id: int) -> str:
    return CANVAS_TURN_LOCK_KEY_TEMPLATE.format(project_id=project_id)


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
    """创建或重新订阅一次 Canvas Agent 执行（断点续传 / Last-Event-ID）。"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    if body.client_turn_id and await canvas_turn_already_completed(project_id, body.client_turn_id):
        raise AppError(
            ErrorCode.CANVAS_DUPLICATE_TURN,
            "canvas turn already completed",
            details={"client_turn_id": body.client_turn_id},
        )

    request_id = str(body.request_id)
    proposed_turn_id = uuid4().hex
    claim = await replay_store.claim(
        request_id=request_id,
        user_id=user_id,
        session_id=project_id,
        turn_id=proposed_turn_id,
        kind=ReplayRequestKind.TURN,
        fingerprint=build_request_fingerprint(
            kind=ReplayRequestKind.TURN,
            user_id=user_id,
            session_id=project_id,
            body=body,
        ),
    )
    try:
        validate_replay_cursor(claim.meta, request.headers.get("Last-Event-ID"))
    except AppError:
        if claim.created:
            await replay_store.discard_starting(request_id)
        raise

    if claim.created:
        cancel_event = asyncio.Event()
        try:
            await project_turn_lock.acquire(
                project_id,
                proposed_turn_id,
                cancel_event=cancel_event,
            )
        except Exception:
            await replay_store.discard_starting(request_id)
            raise
        execution_supervisor.start(
            run_canvas_replay_execution(
                request_id=request_id,
                project_id=project_id,
                turn_id=proposed_turn_id,
                cancel_event=cancel_event,
                stream_factory=lambda: stream_canvas_turn(
                    project_id=project_id,
                    user_id=user_id,
                    content=body.content,
                    model_key=body.model_key or "",
                    client_turn_id=body.client_turn_id,
                    mode=body.mode,
                    enable_tools=body.enable_tools,
                    cancel_event=cancel_event,
                    turn_id=proposed_turn_id,
                    lock_held=True,
                ),
            )
        )

    logger.info(
        "canvas.turn.replay_claim",
        request_id=request_id,
        turn_id=claim.meta.turn_id,
        created=claim.created,
        kind=ReplayRequestKind.TURN.value,
    )
    return _stream_response(claim.meta, request)


@router.post("/{project_id}/turn/resume")
async def canvas_turn_resume(
    project_id: int,
    request: Request,
    body: CanvasResumeRequest,
) -> StreamingResponse:
    """创建或重新订阅一次 HITL 恢复执行。"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    turn_id = body.client_turn_id or uuid4().hex
    request_id = str(body.request_id)
    claim = await replay_store.claim(
        request_id=request_id,
        user_id=user_id,
        session_id=project_id,
        turn_id=turn_id,
        kind=ReplayRequestKind.RESUME,
        fingerprint=build_request_fingerprint(
            kind=ReplayRequestKind.RESUME,
            user_id=user_id,
            session_id=project_id,
            body=body,
        ),
    )
    try:
        validate_replay_cursor(claim.meta, request.headers.get("Last-Event-ID"))
    except AppError:
        if claim.created:
            await replay_store.discard_starting(request_id)
        raise

    if claim.created:
        cancel_event = asyncio.Event()
        try:
            await project_turn_lock.acquire(
                project_id,
                turn_id,
                cancel_event=cancel_event,
            )
        except Exception:
            await replay_store.discard_starting(request_id)
            raise
        execution_supervisor.start(
            run_canvas_replay_execution(
                request_id=request_id,
                project_id=project_id,
                turn_id=turn_id,
                cancel_event=cancel_event,
                stream_factory=lambda: stream_canvas_resume(
                    project_id=project_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    tool_call_id=body.tool_call_id,
                    action=body.action,
                    cancel_event=cancel_event,
                    lock_held=True,
                ),
            )
        )

    logger.info(
        "canvas.turn.replay_claim",
        request_id=request_id,
        turn_id=claim.meta.turn_id,
        created=claim.created,
        kind=ReplayRequestKind.RESUME.value,
    )
    return _stream_response(claim.meta, request)


@router.post("/{project_id}/turn/reconnect")
async def canvas_turn_reconnect(
    project_id: int,
    request: Request,
    body: CanvasReconnectRequest,
) -> StreamingResponse:
    """从 Last-Event-ID 之后重新订阅既有执行。"""
    user_id: int = request.state.user_id
    await project_service.require_owned(user_id, project_id)
    meta = await replay_store.require_owned_meta(
        str(body.request_id),
        user_id=user_id,
        session_id=project_id,
    )
    logger.info(
        "canvas.turn.reconnect",
        request_id=meta.request_id,
        turn_id=meta.turn_id,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
    return _stream_response(meta, request)


@router.post("/{project_id}/turn/cancel")
async def canvas_turn_cancel(project_id: int, request: Request) -> Response[dict]:
    """显式取消当前项目正在运行的 Agent turn（断连不会取消）。"""
    await project_service.require_owned(request.state.user_id, project_id)
    active = await project_turn_lock.active_turn(project_id)
    if active is not None:
        await replay_store.signal_cancel(session_id=project_id, turn_id=active)
        logger.info("canvas.turn.cancel_requested", project_id=project_id, turn_id=active)
        await project_turn_lock.cancel_and_wait(
            project_id,
            timeout_sec=settings.CANVAS_TURN_CANCEL_WAIT_SEC,
        )
        logger.info("canvas.turn.cancel_completed", project_id=project_id, turn_id=active)
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
