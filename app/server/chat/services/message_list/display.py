"""Transform persisted chat rows into UI-oriented list views."""

from __future__ import annotations

from app.server.chat.services.constants import USER_MESSAGE_SECTION_HEADER
from app.server.chat.services.message_list.constants import LIST_METADATA_OMIT_KEYS, TOOL_LIST_CONTENT_MAX_CHARS
from app.server.chat.schemas import ChatMessageView
from app.server.chat.domain.enums import ChatMessageRole


def extract_user_display_content(stored_content: str) -> str:
    """Return the user-visible text; storage may embed turn_context for the model."""
    text = stored_content or ""
    marker = USER_MESSAGE_SECTION_HEADER
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def _compact_tool_content(view: ChatMessageView) -> str:
    preview = view.metadata.get("result_preview")
    if isinstance(preview, str) and preview.strip():
        return preview.strip()
    content = view.content or ""
    if len(content) <= TOOL_LIST_CONTENT_MAX_CHARS:
        return content
    return f"{content[:TOOL_LIST_CONTENT_MAX_CHARS]}…"


def _strip_list_metadata(metadata: dict) -> dict:
    if not metadata:
        return {}
    return {key: value for key, value in metadata.items() if key not in LIST_METADATA_OMIT_KEYS}


def present_message_for_list(view: ChatMessageView, *, stored_role: int) -> ChatMessageView:
    """Apply list-view projections without mutating persistence."""
    metadata = _strip_list_metadata(dict(view.metadata or {}))
    content = view.content
    if stored_role == int(ChatMessageRole.USER):
        content = extract_user_display_content(content)
    elif stored_role == int(ChatMessageRole.TOOL):
        content = _compact_tool_content(
            ChatMessageView(
                id=view.id,
                role=view.role,
                content=view.content,
                metadata=metadata,
                created_at=view.created_at,
            )
        )
    return ChatMessageView(
        id=view.id,
        role=view.role,
        content=content,
        metadata=metadata,
        created_at=view.created_at,
    )
