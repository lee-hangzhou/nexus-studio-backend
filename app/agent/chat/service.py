import asyncio
from typing import AsyncIterator, Optional
from uuid import uuid4

from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.constants import CHAT_CHECKPOINT_THREAD_PREFIX, DEFAULT_CONVERSATION_TITLE
from app.agent.chat.llm.registry import get_model_spec, list_models_for_api
from app.server.chat.services.message_list import list_messages_for_ui
from app.server.chat.schemas import (
    ChatMessageView,
    ConversationListResponse,
    ConversationView,
    MessageListResponse,
)
from app.agent.chat.turn.abort import TurnAbortReason, abort_user_turn, clear_stale_gate_ephemeral
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.orchestrator import stream_turn
from app.agent.chat.turn.resume import stream_chat_resume
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.agent.chat.gate import session_bridge
from app.agent.chat.gate.pending import get_gate_pending, get_gate_pending_many
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.server.infra.config import settings
from app.server.infra.logger import log_exception, logger
from app.server.chat.domain.enums import ChatConversationStatus, ChatMessageRole
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.persistence.messages import ChatMessages


def require_turn_model(model: str | None) -> str:
    key = str(model or "").strip()
    if not key:
        raise AppError(ErrorCode.INVALID_PARAMS, "model is required")
    get_model_spec(key)
    return key


class ChatService:
    async def list_models(self) -> list[dict[str, str]]:
        try:
            return await list_models_for_api()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.INTERNAL_ERROR, f"failed to list chat models: {exc}") from exc

    async def create_conversation(self, user_id: int, title: Optional[str], model: Optional[str]) -> ConversationView:
        model_key = model or settings.CHAT_DEFAULT_MODEL
        get_model_spec(model_key)
        row = await ChatConversations.create(
            user_id=user_id,
            title=title or DEFAULT_CONVERSATION_TITLE,
            default_model=model_key,
            status=int(ChatConversationStatus.ACTIVE),
        )
        return self._to_conversation_view(row)

    async def list_conversations(self, user_id: int, offset: int, limit: int) -> ConversationListResponse:
        query = ChatConversations.filter(user_id=user_id, status=int(ChatConversationStatus.ACTIVE))
        total = await query.count()
        rows = await query.order_by("-updated_at").offset(offset).limit(limit)
        pending_by_id = await get_gate_pending_many([row.id for row in rows])
        items = [
            self._to_conversation_view(row, gate_pending=pending_by_id.get(row.id))
            for row in rows
        ]
        return ConversationListResponse(
            items=items,
            total=total,
        )

    async def get_conversation(self, user_id: int, conversation_id: int) -> ConversationView:
        row = await self._get_owned_conversation(user_id, conversation_id)
        pending = await get_gate_pending(row.id)
        return self._to_conversation_view(row, gate_pending=pending)

    async def update_conversation(
        self,
        user_id: int,
        conversation_id: int,
        title: Optional[str],
        model: Optional[str],
    ) -> ConversationView:
        row = await self._get_owned_conversation(user_id, conversation_id)
        if title is not None:
            row.title = title
        if model is not None:
            get_model_spec(model)
            row.default_model = model
        await row.save()
        pending = await get_gate_pending(row.id)
        return self._to_conversation_view(row, gate_pending=pending)

    async def delete_conversation(self, user_id: int, conversation_id: int) -> None:
        row = await self._get_owned_conversation(user_id, conversation_id)
        saver = get_chat_checkpointer()
        await saver.adelete_thread(f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{conversation_id}")
        row.status = int(ChatConversationStatus.CLOSED)
        await row.save(update_fields=["status", "updated_at"])

    async def list_messages(
        self,
        user_id: int,
        conversation_id: int,
        before_id: Optional[int],
        limit: int,
    ) -> MessageListResponse:
        await self._get_owned_conversation(user_id, conversation_id)
        response = await list_messages_for_ui(
            conversation_id=conversation_id,
            before_id=before_id,
            turn_limit=limit,
            attachments_map_fn=self._attachments_map,
            to_base_view=self._to_message_view,
        )
        self._hydrate_attachment_urls(response.items)
        return response

    async def begin_turn(
        self,
        *,
        user_id: int,
        conversation_id: int,
        model: Optional[str],
    ) -> tuple[ChatConversations, str, str]:
        """校验归属并抢占会话锁。

        必须在返回流式响应之前调用：锁冲突（CONVERSATION_BUSY）在此处以普通异常抛出，
        可被全局异常处理器转成 409 JSON，而不是在 SSE 响应已开始后才报错。
        """
        conversation = await self._get_owned_conversation(user_id, conversation_id)
        pending = await get_gate_pending(conversation_id)
        if pending:
            status = str(pending.get("status") or "pending")
            if status == "pending":
                raise AppError(
                    ErrorCode.CONVERSATION_BUSY,
                    f"conversation {conversation_id} awaiting user gate",
                    details={"reason": "gate_pending", **pending},
                )
            await clear_stale_gate_ephemeral(conversation_id, user_id=user_id)
        model_key = require_turn_model(model)
        turn_id = str(uuid4())
        await conversation_turn_lock.acquire(conversation_id, turn_id)
        return conversation, model_key, turn_id

    async def begin_resume_turn(
        self,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        model: str | None,
    ) -> tuple[ChatConversations, str, str]:
        conversation = await self._get_owned_conversation(user_id, conversation_id)
        pending = await get_gate_pending(conversation_id)
        if not pending or pending.get("turn_id") != turn_id:
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "no pending gate for this turn",
            )
        model_key = require_turn_model(model)
        pending_model = str(pending.get("model_key") or "").strip()
        if pending_model and pending_model != model_key:
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                f"model mismatch for turn resume: expected {pending_model}",
            )
        await conversation_turn_lock.acquire(conversation_id, turn_id)
        return conversation, model_key, turn_id

    async def get_gate_state(self, user_id: int, conversation_id: int) -> dict:
        await self._get_owned_conversation(user_id, conversation_id)
        pending = await get_gate_pending(conversation_id)
        if pending and pending.get("status") != "pending":
            return {"pending": None}
        if pending and pending.get("gate_type") == "session_bridge":
            gate_id = str(pending.get("gate_id") or "")
            bridge_status = await session_bridge.get_bridge_status_by_gate(gate_id)
            if bridge_status:
                pending = {**pending, "bridge_status": bridge_status.get("status")}
        return {"pending": pending}

    async def cancel_gate(
        self,
        user_id: int,
        conversation_id: int,
        *,
        turn_id: str,
        gate_id: str,
    ) -> dict:
        conversation = await self._get_owned_conversation(user_id, conversation_id)
        pending = await get_gate_pending(conversation_id)
        if not pending or pending.get("turn_id") != turn_id or pending.get("gate_id") != gate_id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "gate not pending")
        status = str(pending.get("status") or "pending")
        if status not in {"pending", "submitted"}:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "gate not pending")
        result = await abort_user_turn(
            user_id=user_id,
            conversation=conversation,
            reason=TurnAbortReason.GATE_CANCEL,
            turn_id=turn_id,
            gate_id=gate_id,
        )
        return {"ok": True, "message_id": result.message_id}

    async def stream_resume(
        self,
        *,
        conversation: ChatConversations,
        model_key: str,
        turn_id: str,
        user_id: int,
        conversation_id: int,
        gate_id: str,
        action: str,
        fields: dict | None,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[str]:
        try:
            async for chunk in stream_chat_resume(
                conversation=conversation,
                user_id=user_id,
                conversation_id=conversation_id,
                model_key=model_key,
                turn_id=turn_id,
                gate_id=gate_id,
                action=action,
                fields=fields,
                cancel_event=cancel_event,
                lock_held=True,
            ):
                yield chunk
        finally:
            try:
                await conversation_turn_lock.release(conversation_id, turn_id)
            except Exception:
                logger.exception(
                    "chat.stream_resume.release_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )

    async def stream_turn(
        self,
        *,
        conversation: ChatConversations,
        model_key: str,
        turn_id: str,
        user_id: int,
        conversation_id: int,
        content: str,
        attachment_ids: list[int],
        enable_tools: bool = True,
        client_turn_id: Optional[str] = None,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[str]:
        logger.info("chat.stream_turn.start", conversation_id=conversation_id, model=model_key, turn=turn_id)
        try:
            async for chunk in stream_turn(
                conversation=conversation,
                user_id=user_id,
                conversation_id=conversation_id,
                content=content,
                model_key=model_key,
                attachment_ids=attachment_ids,
                enable_tools=enable_tools,
                client_turn_id=client_turn_id,
                cancel_event=cancel_event,
                turn_id=turn_id,
            ):
                yield chunk
        except Exception as exc:
            log_exception(
                "chat.stream_turn.generator_failed",
                exc=exc,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            raise
        finally:
            # 兜底释放：无论正常结束、异常、客户端断连（GeneratorExit）还是取消，
            # 都保证会话锁被释放，避免会话被永久锁成 busy。
            try:
                await conversation_turn_lock.release(conversation_id, turn_id)
            except Exception:
                logger.exception(
                    "chat.stream_turn.release_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )

    async def cancel_active_turn(self, user_id: int, conversation_id: int) -> dict:
        conversation = await self._get_owned_conversation(user_id, conversation_id)
        result = await abort_user_turn(
            user_id=user_id,
            conversation=conversation,
            reason=TurnAbortReason.USER_CANCEL,
        )
        return {"ok": True, "cancelled_turn_id": result.turn_id}

    async def _attachments_map(self, rows: list[ChatMessages]) -> dict[int, list[dict]]:
        """批量加载多条消息的附件，返回 message_id -> [附件 dict]，消除逐条查询的 N+1。

        附件 dict 含 storage_key、不含 preview_url：签名有时效，留到 hydrate 阶段现算，
        以便历史页缓存命中时也能拿到新鲜签名。保持原「先按 message_id 直连、再按
        metadata.attachment_ids 兜底」的语义与 created_at 排序。
        """
        if not rows:
            return {}

        msg_ids = [row.id for row in rows]
        by_message: dict[int, list[ChatAttachments]] = {}
        direct_rows = await ChatAttachments.filter(message_id__in=msg_ids).order_by("created_at")
        for att in direct_rows:
            by_message.setdefault(att.message_id, []).append(att)

        # 仅对没有 message_id 直连附件的消息，走 metadata.attachment_ids 兜底
        fallback_ids_by_message: dict[int, list[int]] = {}
        all_fallback_ids: set[int] = set()
        for row in rows:
            if row.id in by_message:
                continue
            ids = (row.metadata or {}).get("attachment_ids") or []
            if ids:
                fallback_ids_by_message[row.id] = ids
                all_fallback_ids.update(ids)

        fallback_rows: list[ChatAttachments] = []
        if all_fallback_ids:
            fallback_rows = await ChatAttachments.filter(id__in=list(all_fallback_ids)).order_by("created_at")

        result: dict[int, list[dict]] = {}
        for row in rows:
            atts = by_message.get(row.id)
            if atts is None and row.id in fallback_ids_by_message:
                wanted = set(fallback_ids_by_message[row.id])
                atts = [fb for fb in fallback_rows if fb.id in wanted]
            if not atts:
                continue
            result[row.id] = [
                {
                    "attachment_id": att.id,
                    "filename": att.filename,
                    "mime_type": att.mime_type,
                    "storage_key": att.storage_key,
                }
                for att in atts
            ]
        return result

    def _hydrate_attachment_urls(self, views: list[ChatMessageView]) -> None:
        """读出后统一现算 preview_url 并剥离 storage_key（签名不入缓存、不下发裸 key）。"""
        for view in views:
            attachments = view.metadata.get("attachments")
            if not isinstance(attachments, list):
                continue
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                storage_key = att.pop("storage_key", None)
                if storage_key and not att.get("preview_url"):
                    att["preview_url"] = chat_attachment_service.build_preview_url(storage_key)

    async def _get_owned_conversation(self, user_id: int, conversation_id: int) -> ChatConversations:
        row = await ChatConversations.get_or_none(id=conversation_id, user_id=user_id)
        if row is None or row.status != int(ChatConversationStatus.ACTIVE):
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "conversation not found")
        return row

    def _to_message_view(self, row: ChatMessages, attachments: list[dict]) -> ChatMessageView:
        role_map = {
            int(ChatMessageRole.USER): "user",
            int(ChatMessageRole.ASSISTANT): "assistant",
            int(ChatMessageRole.SYSTEM): "system",
            int(ChatMessageRole.TOOL): "tool",
        }
        metadata = dict(row.metadata or {})
        if attachments:
            metadata["attachments"] = attachments
        return ChatMessageView(
            id=row.id,
            role=role_map.get(row.role, "assistant"),
            content=row.content,
            metadata=metadata,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_conversation_view(
        row: ChatConversations,
        *,
        gate_pending: dict | None = None,
    ) -> ConversationView:
        is_generating = bool(getattr(row, "active_turn_id", None))
        return ConversationView(
            id=row.id,
            title=row.title,
            default_model=row.default_model,
            status=row.status,
            is_generating=is_generating,
            awaiting_user_gate=bool(
                gate_pending and str(gate_pending.get("status") or "pending") == "pending"
            ),
            generating_started_at=row.active_turn_started_at if is_generating else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
