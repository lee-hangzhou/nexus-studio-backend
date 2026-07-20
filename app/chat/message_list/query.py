"""Turn-anchored pagination over append-only chat message rows."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.chat_enums import ChatMessageRole
from app.models.chat_messages import ChatMessages


@dataclass(frozen=True)
class MessageListPageRows:
    """Raw rows for one list page, oldest-first."""

    rows: list[ChatMessages]
    has_more: bool
    next_before_id: int | None


async def fetch_turn_anchored_page(
    *,
    conversation_id: int,
    before_id: int | None,
    turn_limit: int,
) -> MessageListPageRows:
    """Load up to ``turn_limit`` user turns and all messages within their id span.

    Pagination cursor ``before_id`` is exclusive on message id (load strictly older turns).
    ``next_before_id`` is the oldest user message id in this page when ``has_more``.
    """
    user_role = int(ChatMessageRole.USER)
    anchor_query = ChatMessages.filter(conversation_id=conversation_id, role=user_role)
    if before_id is not None:
        anchor_query = anchor_query.filter(id__lt=before_id)

    anchors = list(await anchor_query.order_by("-id").limit(turn_limit))
    if not anchors:
        return MessageListPageRows(rows=[], has_more=False, next_before_id=None)

    oldest_user_id = min(row.id for row in anchors)
    span_query = ChatMessages.filter(conversation_id=conversation_id, id__gte=oldest_user_id)
    if before_id is not None:
        span_query = span_query.filter(id__lt=before_id)

    rows = list(await span_query.order_by("id"))
    has_more = await ChatMessages.filter(
        conversation_id=conversation_id,
        role=user_role,
        id__lt=oldest_user_id,
    ).exists()

    return MessageListPageRows(
        rows=rows,
        has_more=has_more,
        next_before_id=oldest_user_id if has_more else None,
    )
