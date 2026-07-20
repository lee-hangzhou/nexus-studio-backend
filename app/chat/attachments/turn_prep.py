"""Turn-start attachment binding."""

from __future__ import annotations

from app.chat.attachments.service import chat_attachment_service
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.chat_attachments import ChatAttachments


async def apply_attachment_intent(user_id: int, conversation_id: int, attachment_ids: list[int]) -> None:
    if not attachment_ids:
        return
    rows = await ChatAttachments.filter(
        id__in=attachment_ids,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if len(rows) != len(set(attachment_ids)):
        raise AppError(ErrorCode.INVALID_PARAMS, "attachment not found in conversation")
    for attachment_id in set(attachment_ids):
        await chat_attachment_service.attach(
            user_id=user_id,
            conversation_id=conversation_id,
            attachment_id=attachment_id,
        )
