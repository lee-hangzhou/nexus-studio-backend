import asyncio

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import Response as FastAPIResponse, StreamingResponse

from app.chat.attachments.service import chat_attachment_service
from app.chat.attachments.status import attachment_source
from app.chat.capabilities import validate_upload_size, validate_upload_type
from app.chat.browser.state_capture import read_browser_state_asset_bytes
from app.chat.gate import pending as gate_pending_store
from app.chat.gate import meta as gate_meta_store
from app.chat.gate.assets import read_gate_asset_file, refresh_gate_asset
from app.chat.gate.qr_verify import GateCaptureError
from app.chat.workspace import conversation_workspace
from app.chat.schemas import (
    AttachmentIdRequest,
    AttachmentPreviewResponse,
    AttachmentView,
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
    MessageListRequest,
    MessageListResponse,
    MessageStreamRequest,
    TurnCancelRequest,
    TurnResumeRequest,
    GateStateRequest,
    GateCancelRequest,
    GateAssetRefreshRequest,
    BridgeCreateRequest,
    BridgeImportRequest,
)
from app.composition import chat_service
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.chat_attachments import ChatAttachments
from app.schemas.base import Response

router = APIRouter()


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
    cancel_event = asyncio.Event()

    # 在返回流式响应之前抢锁：会话忙时（CONVERSATION_BUSY）会在此抛出，
    # 由全局异常处理器返回 409 JSON，而不是在 SSE 已开始后崩成 500。
    conversation, model_key, turn_id = await chat_service.begin_turn(
        user_id=user_id,
        conversation_id=body.conversation_id,
        model=body.model,
    )

    async def event_generator():
        async for chunk in chat_service.stream_turn(
            conversation=conversation,
            model_key=model_key,
            turn_id=turn_id,
            user_id=user_id,
            conversation_id=body.conversation_id,
            content=body.content,
            attachment_ids=body.attachment_ids,
            enable_tools=body.enable_tools,
            client_turn_id=body.client_turn_id,
            cancel_event=cancel_event,
        ):
            if await request.is_disconnected():
                cancel_event.set()
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/turn/cancel")
async def cancel_turn(request: Request, body: TurnCancelRequest) -> Response[dict]:
    user_id: int = request.state.user_id
    result = await chat_service.cancel_active_turn(user_id, body.conversation_id)
    return Response(data=result)


@router.post("/turn/resume")
async def resume_turn(request: Request, body: TurnResumeRequest) -> StreamingResponse:
    user_id: int = request.state.user_id
    cancel_event = asyncio.Event()
    conversation, model_key, turn_id = await chat_service.begin_resume_turn(
        user_id=user_id,
        conversation_id=body.conversation_id,
        turn_id=body.turn_id,
        model=body.model,
    )
    if body.action == "submit":
        await gate_pending_store.update_gate_pending_status(body.conversation_id, "resuming")

    async def event_generator():
        try:
            async for chunk in chat_service.stream_resume(
                conversation=conversation,
                model_key=model_key,
                turn_id=turn_id,
                user_id=user_id,
                conversation_id=body.conversation_id,
                gate_id=body.gate_id,
                action=body.action,
                fields=body.fields,
                cancel_event=cancel_event,
            ):
                if await request.is_disconnected():
                    cancel_event.set()
                yield chunk
        finally:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
    from app.chat.gate import meta as gate_meta_store

    meta = await gate_meta_store.get_gate_meta(body.gate_id)
    domain = str((meta or {}).get("expected_domain") or pending.get("domain") or "")
    if not domain:
        raise AppError(ErrorCode.INVALID_PARAMS, "missing expected domain")
    from app.chat.gate.session_bridge import create_bridge_token

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
    from app.chat.gate.session_bridge import get_bridge_status_by_gate

    status = await get_bridge_status_by_gate(body.gate_id)
    return Response(data={"status": status or {"status": "unknown"}})


@router.post("/gate/bridge/import")
async def gate_bridge_import(request: Request, body: BridgeImportRequest) -> Response[dict]:
    from app.chat.gate.session_bridge import import_bridge_cookies

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
