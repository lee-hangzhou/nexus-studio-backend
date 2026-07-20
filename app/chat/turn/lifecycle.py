"""Conversation turn active-state updates."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.chat_conversations import ChatConversations


async def mark_turn_active(conversation: ChatConversations, turn_id: str) -> None:
    conversation.active_turn_id = turn_id
    conversation.active_turn_started_at = datetime.now(timezone.utc)
    await conversation.save(update_fields=["active_turn_id", "active_turn_started_at", "updated_at"])


async def clear_turn_active(conversation: ChatConversations) -> None:
    conversation.active_turn_id = None
    conversation.active_turn_started_at = None
    await conversation.save(update_fields=["active_turn_id", "active_turn_started_at", "updated_at"])


async def touch_conversation_updated(conversation: ChatConversations) -> None:
    conversation.updated_at = datetime.now(timezone.utc)
    await conversation.save(update_fields=["updated_at"])
