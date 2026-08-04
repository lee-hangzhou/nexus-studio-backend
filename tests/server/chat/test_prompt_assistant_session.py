"""创作提示词助手会话：幂等 get-or-create，且不出现在普通会话列表。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from tortoise import Tortoise

from app.agent.chat.service import ChatService
from app.server.infra.database import db
from app.server.chat.domain.enums import ChatConversationKind
from app.server.chat.persistence.conversations import ChatConversations


@asynccontextmanager
async def _chat_db():
    await db.connect()
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS kind VARCHAR(32) NOT NULL DEFAULT 'chat'"
    )
    await conn.execute_query(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_conversations_user_prompt_assistant
          ON chat_conversations (user_id)
          WHERE kind = 'prompt_assistant' AND status = 1
        """
    )
    try:
        yield
    finally:
        await db.disconnect()


@pytest.mark.integration
async def test_prompt_assistant_session_idempotent_and_hidden_from_chat_list() -> None:
    user_id = 9_100_000 + (uuid4().int % 100_000)
    service = ChatService()
    model_key = f"pa-model-{uuid4().hex[:8]}"
    created_ids: list[int] = []

    with (
        patch("app.agent.chat.service.require_turn_model", side_effect=lambda m: str(m).strip()),
        patch("app.agent.chat.service.get_gate_pending_many", return_value={}),
        patch(
            "app.agent.chat.service.UpgradeInviteService.conversation_ids_with_pending",
            return_value=set(),
        ),
    ):
        async with _chat_db():
            try:
                first = await service.get_or_create_prompt_assistant_session(
                    user_id, model=model_key
                )
                second = await service.get_or_create_prompt_assistant_session(
                    user_id, model=model_key
                )
                created_ids.extend([first.id, second.id])
                assert first.id == second.id
                assert first.kind is ChatConversationKind.PROMPT_ASSISTANT
                assert first.title == "创作提示词助手"

                chat = await service.create_conversation(user_id, "普通会话", model_key)
                created_ids.append(chat.id)
                listed = await service.list_conversations(user_id, offset=0, limit=50)
                ids = {item.id for item in listed.items}
                assert chat.id in ids
                assert first.id not in ids
            finally:
                if created_ids:
                    await ChatConversations.filter(
                        id__in=list(set(created_ids)),
                        user_id=user_id,
                    ).delete()
