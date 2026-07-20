"""Attachment status helpers."""

from __future__ import annotations

from app.domain.chat_enums import AttachmentSource, ChatAttachmentStatus


def attachment_status_label(status: int) -> str:
    try:
        return ChatAttachmentStatus(status).name.lower()
    except ValueError:
        return "unknown"


def attachment_source(row) -> str:
    return getattr(row, "source", None) or AttachmentSource.USER_UPLOAD.value
