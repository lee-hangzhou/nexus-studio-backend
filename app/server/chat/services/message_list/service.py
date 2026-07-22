"""Orchestrate turn-based message list queries and UI projections."""

from __future__ import annotations

import copy
from typing import Callable, Awaitable

from app.server.chat.services.message_list.constants import DEFAULT_TURN_LIMIT, LIST_CACHE_KEY_PREFIX, MAX_TURN_LIMIT
from app.server.chat.services.message_list.display import present_message_for_list
from app.server.chat.services.message_list.query import fetch_turn_anchored_page
from app.server.chat.schemas import ChatMessageView, MessageListResponse
from app.server.infra.cache import app_cache
from app.server.infra.config import settings
from app.server.chat.persistence.messages import ChatMessages


def clamp_turn_limit(limit: int) -> int:
    return max(1, min(limit, MAX_TURN_LIMIT))


def list_cache_key(conversation_id: int, before_id: int, turn_limit: int) -> str:
    return f"{LIST_CACHE_KEY_PREFIX}:{conversation_id}:{before_id}:{turn_limit}"


class MessageListAssembler:
    """Build list views from rows; attachments resolved by caller."""

    def __init__(
        self,
        *,
        to_base_view: Callable[[ChatMessages, list[dict]], ChatMessageView],
    ) -> None:
        self._to_base_view = to_base_view

    def assemble(self, rows: list[ChatMessages], attachments_map: dict[int, list[dict]]) -> list[ChatMessageView]:
        views: list[ChatMessageView] = []
        for row in rows:
            base = self._to_base_view(row, attachments_map.get(row.id, []))
            views.append(present_message_for_list(base, stored_role=row.role))
        return views


async def list_messages_for_ui(
    *,
    conversation_id: int,
    before_id: int | None,
    turn_limit: int,
    attachments_map_fn: Callable[[list[ChatMessages]], Awaitable[dict[int, list[dict]]]],
    to_base_view: Callable[[ChatMessages, list[dict]], ChatMessageView],
) -> MessageListResponse:
    turn_limit = clamp_turn_limit(turn_limit)

    async def _compute() -> dict:
        page = await fetch_turn_anchored_page(
            conversation_id=conversation_id,
            before_id=before_id,
            turn_limit=turn_limit,
        )
        attachments_map = await attachments_map_fn(page.rows)
        assembler = MessageListAssembler(to_base_view=to_base_view)
        items = assembler.assemble(page.rows, attachments_map)
        response = MessageListResponse(
            items=items,
            has_more=page.has_more,
            next_before_id=page.next_before_id,
        )
        return response.model_dump(mode="json")

    if before_id is not None:
        key = list_cache_key(conversation_id, before_id, turn_limit)
        raw = await app_cache.get_or_compute(
            key,
            _compute,
            ttl=settings.CHAT_MSG_PAGE_TTL,
            cache_none=False,
        )
    else:
        raw = await _compute()

    response = MessageListResponse(**copy.deepcopy(raw))
    return response
