"""Attachment status helpers."""

from __future__ import annotations

from app.domain.chat_enums import AttachmentSource, ChatAttachmentStatus


def attachment_status_label(status: int) -> str:
    return ChatAttachmentStatus(status).name.lower()


def attachment_source(row) -> str:
    return AttachmentSource(row.source).value
