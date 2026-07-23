import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import Response as FastAPIResponse, StreamingResponse

from app.agent.chat.browser.state_capture import read_browser_state_asset_bytes
from app.agent.chat.gate import meta as gate_meta_store
from app.agent.chat.gate import pending as gate_pending_store
from app.agent.chat.gate.assets import read_gate_asset_file, refresh_gate_asset
from app.agent.chat.gate.qr_verify import GateCaptureError
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.replay_execution import run_chat_replay_execution
from app.agent.chat.workspace import conversation_workspace
from app.agent.runtime.stream.replay import (
    ReplayMeta,
    ReplayRequestKind,
    build_request_fingerprint,
    execution_supervisor,
    replay_store,
    stream_replay,
    validate_replay_cursor,
)
from app.composition import chat_service
from app.server.api.schemas import Response
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.schemas import (
    AttachmentIdRequest,
    AttachmentPreviewResponse,
    AttachmentView,
    BridgeCreateRequest,
    BridgeImportRequest,
    ChatMessageView,
    ChatModelItem,
    ConversationAttachmentActionRequest,
    ConversationAttachmentRequest,
    ConversationCreateRequest,
    ConversationIdRequest,
    ConversationListRequest,
    ConversationListResponse,
    ConversationUpdateRequest,
    ConversationView,
    GateAssetRefreshRequest,
    GateCancelRequest,
    GateStateRequest,
    MessageListRequest,
    MessageListResponse,
    MessageStreamRequest,
    StreamReconnectRequest,
    TurnCancelRequest,
    TurnResumeRequest,
)
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.attachments.status import attachment_source
from app.server.chat.services.capabilities import validate_upload_size, validate_upload_type
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

router = APIRouter()


def _chat_stream_response(meta: ReplayMeta, request: Request) -> StreamingResponse:
    last_event_id = request.headers.get("Last-Event-ID")
    validate_replay_cursor(meta, last_event_id)

    async def gen() -> AsyncIterator[str]:
        async for chunk in stream_replay(
            replay_store,
            meta,
            last_event_id=last_event_id,
            execution_lock_key=f"chat:turn_lock:{meta.session_id}",
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


@router.post("/model/list")
async def list_models() -> Response[list[ChatModelItem]]:
    items = [ChatModelItem(**item) for item in await chat_service.list_models()]
    return Response(data=items)


@router.post("/conversation/create")
async def create_conversation(request: Request, body: ConversationCreateRequest) -> Response[ConversationView]:
    user_id: int = request.state.user_id
    result = await chat_service.create_conversation(user_id, body.title, body.model)
    return Response(data=result)


@router.post("/conversation/list")
async def list_conversations(request: Request, body: ConversationListRequest) -> Response[ConversationListResponse]:
    user_id: int = request.state.user_id
    result = await chat_service.list_conversations(user_id, body.offset, body.limit)
    return Response(data=result)


@router.post("/conversation/get")
async def get_conversation(request: Request, body: ConversationIdRequest) -> Response[ConversationView]:
    user_id: int = request.state.user_id
    result = await chat_service.get_conversation(user_id, body.conversation_id)
    return Response(data=result)


@router.post("/conversation/update")
async def update_conversation(request: Request, body: ConversationUpdateRequest) -> Response[ConversationView]:
    user_id: int = request.state.user_id
    result = await chat_service.update_conversation(user_id, body.conversation_id, body.title, body.model)
    return Response(data=result)


@router.post("/conversation/delete")
async def delete_conversation(request: Request, body: ConversationIdRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.delete_conversation(user_id, body.conversation_id)
    return Response(data={"ok": True})


@router.post("/message/list")
async def list_messages(request: Request, body: MessageListRequest) -> Response[MessageListResponse]:
    user_id: int = request.state.user_id
    result = await chat_service.list_messages(user_id, body.conversation_id, body.before_id, body.limit)
    return Response(data=result)


@router.post("/message/stream")
async def stream_message(request: Request, body: MessageStreamRequest) -> StreamingResponse:
    user_id: int = request.state.user_id
    conversation, model_key = await chat_service.prepare_turn(
        user_id=user_id,
        conversation_id=body.conversation_id,
        model=body.model,
    )
    request_id = str(body.request_id)
    proposed_turn_id = str(uuid4())
    claim = await replay_store.claim(
        request_id=request_id,
        user_id=user_id,
        session_id=body.conversation_id,
        turn_id=proposed_turn_id,
        kind=ReplayRequestKind.TURN,
        fingerprint=build_request_fingerprint(
            kind=ReplayRequestKind.TURN,
            user_id=user_id,
            session_id=body.conversation_id,
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
            await conversation_turn_lock.acquire(
                body.conversation_id,
                proposed_turn_id,
                cancel_event=cancel_event,
            )
        except Exception:
            await replay_store.discard_starting(request_id)
            raise
        execution_supervisor.start(
            run_chat_replay_execution(
                request_id=request_id,
                conversation_id=body.conversation_id,
                turn_id=proposed_turn_id,
                cancel_event=cancel_event,
                stream_factory=lambda: chat_service.stream_turn(
                    conversation=conversation,
                    model_key=model_key,
                    turn_id=proposed_turn_id,
                    user_id=user_id,
                    conversation_id=body.conversation_id,
                    content=body.content,
                    attachment_ids=body.attachment_ids,
                    enable_tools=body.enable_tools,
                    client_turn_id=body.client_turn_id,
                    cancel_event=cancel_event,
                ),
            )
        )

    return _chat_stream_response(claim.meta, request)


@router.post("/turn/cancel")
async def cancel_turn(request: Request, body: TurnCancelRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    result = await chat_service.cancel_active_turn(user_id, body.conversation_id)
    return Response(data=result)


@router.post("/turn/resume")
async def resume_turn(request: Request, body: TurnResumeRequest) -> StreamingResponse:
    user_id: int = request.state.user_id
    conversation, model_key = await chat_service.prepare_resume_turn(
        user_id=user_id,
        conversation_id=body.conversation_id,
        turn_id=body.turn_id,
        model=body.model,
    )
    if body.action == "submit":
        await gate_pending_store.update_gate_pending_status(body.conversation_id, "resuming")

    request_id = str(body.request_id)
    claim = await replay_store.claim(
        request_id=request_id,
        user_id=user_id,
        session_id=body.conversation_id,
        turn_id=body.turn_id,
        kind=ReplayRequestKind.RESUME,
        fingerprint=build_request_fingerprint(
            kind=ReplayRequestKind.RESUME,
            user_id=user_id,
            session_id=body.conversation_id,
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
            await conversation_turn_lock.acquire(
                body.conversation_id,
                body.turn_id,
                cancel_event=cancel_event,
            )
        except Exception:
            await replay_store.discard_starting(request_id)
            raise
        execution_supervisor.start(
            run_chat_replay_execution(
                request_id=request_id,
                conversation_id=body.conversation_id,
                turn_id=body.turn_id,
                cancel_event=cancel_event,
                stream_factory=lambda: chat_service.stream_resume(
                    conversation=conversation,
                    model_key=model_key,
                    turn_id=body.turn_id,
                    user_id=user_id,
                    conversation_id=body.conversation_id,
                    gate_id=body.gate_id,
                    action=body.action,
                    fields=body.fields,
                    cancel_event=cancel_event,
                ),
            )
        )

    return _chat_stream_response(claim.meta, request)


@router.post("/turn/reconnect")
async def reconnect_turn(request: Request, body: StreamReconnectRequest) -> StreamingResponse:
    user_id: int = request.state.user_id
    await chat_service.require_owned(user_id, body.conversation_id)
    meta = await replay_store.require_owned_meta(
        str(body.request_id),
        user_id=user_id,
        session_id=body.conversation_id,
    )
    return _chat_stream_response(meta, request)


@router.post("/gate/state")
async def gate_state(request: Request, body: GateStateRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    result = await chat_service.get_gate_state(user_id, body.conversation_id)
    return Response(data=result)


@router.post("/gate/cancel")
async def gate_cancel(request: Request, body: GateCancelRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    result = await chat_service.cancel_gate(
        user_id,
        body.conversation_id,
        turn_id=body.turn_id,
        gate_id=body.gate_id,
    )
    return Response(data=result)


@router.get("/gate/asset")
async def gate_asset(
    request: Request,
    conversation_id: int = Query(...),
    gate_id: str = Query(...),
    kind: str | None = Query(None),
) -> FastAPIResponse:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, conversation_id)
    pending = await gate_pending_store.get_gate_pending(conversation_id)
    if not pending or str(pending.get("gate_id") or "") != gate_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "gate not pending")
    workspace = conversation_workspace(user_id, conversation_id)
    asset_kind = kind or "legacy"
    try:
        data = read_gate_asset_file(workspace, gate_id, asset_kind)
    except FileNotFoundError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc
    return FastAPIResponse(content=data, media_type="image/png")


@router.post("/gate/asset/refresh")
async def gate_asset_refresh(request: Request, body: GateAssetRefreshRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    pending = await gate_pending_store.get_gate_pending(body.conversation_id)
    if not pending or str(pending.get("gate_id") or "") != body.gate_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "gate not pending")
    workspace = conversation_workspace(user_id, body.conversation_id)
    try:
        await refresh_gate_asset(
            conversation_id=body.conversation_id,
            workspace=workspace,
            gate_id=body.gate_id,
        )
    except PermissionError as exc:
        raise AppError(ErrorCode.PERMISSION_DENIED, str(exc)) from exc
    except FileNotFoundError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc
    except RuntimeError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except GateCaptureError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, exc.detail or exc.error_type) from exc
    return Response(data={"ok": True})


@router.post("/gate/bridge/create")
async def gate_bridge_create(request: Request, body: BridgeCreateRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    pending = await gate_pending_store.get_gate_pending(body.conversation_id)
    if not pending or str(pending.get("gate_id") or "") != body.gate_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "gate not pending")
    if pending.get("gate_type") != "session_bridge":
        raise AppError(ErrorCode.INVALID_PARAMS, "gate is not session_bridge")
    from app.agent.chat.gate import meta as gate_meta_store

    meta = await gate_meta_store.get_gate_meta(body.gate_id)
    domain = str((meta or {}).get("expected_domain") or pending.get("domain") or "")
    if not domain:
        raise AppError(ErrorCode.INVALID_PARAMS, "missing expected domain")
    from app.agent.chat.gate.session_bridge import create_bridge_token

    result = await create_bridge_token(
        user_id=user_id,
        conversation_id=body.conversation_id,
        gate_id=body.gate_id,
        expected_domain=domain,
    )
    return Response(data=result)


@router.post("/gate/bridge/status")
async def gate_bridge_status(request: Request, body: BridgeCreateRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    from app.agent.chat.gate.session_bridge import get_bridge_status_by_gate

    status = await get_bridge_status_by_gate(body.gate_id)
    if status is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "bridge 状态不存在")
    return Response(data={"status": status})


@router.post("/gate/bridge/import")
async def gate_bridge_import(request: Request, body: BridgeImportRequest) -> Response[dict]:
    from app.agent.chat.gate.session_bridge import import_bridge_cookies

    try:
        result = await import_bridge_cookies(
            bridge_token=body.bridge_token,
            cookies=body.cookies,
            page_url=body.page_url,
        )
    except PermissionError as exc:
        raise AppError(ErrorCode.PERMISSION_DENIED, str(exc)) from exc
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
    return Response(data=result)


@router.get("/browser/state/asset")
async def browser_state_asset(
    request: Request,
    conversation_id: int = Query(...),
    path: str = Query(...),
) -> FastAPIResponse:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, conversation_id)
    workspace = conversation_workspace(user_id, conversation_id)
    try:
        data = await read_browser_state_asset_bytes(workspace=workspace, path=path)
    except PermissionError as exc:
        raise AppError(ErrorCode.PERMISSION_DENIED, str(exc)) from exc
    except FileNotFoundError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc
    return FastAPIResponse(content=data, media_type="image/png")


@router.post("/attachment/upload")
async def upload_attachment(
    request: Request,
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, conversation_id)
    validate_upload_type(file.filename or "", file.content_type or "")
    await file.seek(0)
    raw = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    validate_upload_size(filename=file.filename or "file", mime_type=mime_type, size=len(raw))
    row = await chat_attachment_service.upload_and_enqueue(
        user_id=user_id,
        conversation_id=conversation_id,
        filename=file.filename or "file",
        mime_type=mime_type,
        raw_bytes=raw,
    )
    return Response(
        data={
            "attachment_id": row.id,
            "filename": row.filename,
            "mime_type": row.mime_type,
            "status": row.status,
            "source": attachment_source(row),
        }
    )


def _attachment_view_from_row(row: ChatAttachments) -> AttachmentView:
    return AttachmentView(
        id=row.id,
        filename=row.filename,
        mime_type=row.mime_type,
        storage_key=row.storage_key,
        size=row.size,
        status=row.status,
        is_attached=row.is_attached,
        source=attachment_source(row),
    )


@router.post("/attachment/preview-url")
async def preview_attachment_url(request: Request, body: AttachmentIdRequest) -> Response[AttachmentPreviewResponse]:
    user_id: int = request.state.user_id
    row = await ChatAttachments.get_or_none(id=body.attachment_id, user_id=user_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "attachment not found")
    url = chat_attachment_service.build_preview_url(row.storage_key)
    return Response(
        data=AttachmentPreviewResponse(
            attachment_id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            url=url,
        )
    )


@router.post("/attachment/get")
async def get_attachment(request: Request, body: AttachmentIdRequest) -> Response[AttachmentView]:
    user_id: int = request.state.user_id
    row = await ChatAttachments.get_or_none(id=body.attachment_id, user_id=user_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "attachment not found")
    return Response(data=_attachment_view_from_row(row))


@router.post("/attachment/list")
async def list_attachments(request: Request, body: ConversationAttachmentRequest) -> Response[list[AttachmentView]]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    items = await chat_attachment_service.list_attached(user_id=user_id, conversation_id=body.conversation_id)
    return Response(
        data=[
            AttachmentView(
                id=item.id,
                filename=item.filename,
                mime_type=item.mime_type,
                storage_key=item.storage_key,
                size=item.size,
                status=item.status,
                is_attached=item.is_attached,
                source=item.source,
                preview_url=chat_attachment_service.build_preview_url(item.storage_key),
            )
            for item in items
        ]
    )


@router.post("/attachment/detach")
async def detach_attachment(request: Request, body: ConversationAttachmentActionRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    await chat_attachment_service.detach(
        user_id=user_id,
        conversation_id=body.conversation_id,
        attachment_id=body.attachment_id,
    )
    return Response(data={"ok": True})


@router.post("/attachment/attach")
async def attach_attachment(request: Request, body: ConversationAttachmentActionRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    await chat_service.get_conversation(user_id, body.conversation_id)
    await chat_attachment_service.attach(
        user_id=user_id,
        conversation_id=body.conversation_id,
        attachment_id=body.attachment_id,
    )
    return Response(data={"ok": True})
