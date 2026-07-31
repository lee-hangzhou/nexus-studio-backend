from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from tortoise import Tortoise

from app.agent.chat.conversation_title import generate_conversation_title_via_llm
from app.agent.chat.llm.model_catalog import model_catalog
from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations

# 前端创建会话时实际写入的占位标题（与 DEFAULT_CONVERSATION_TITLE 漂移即本 bug）
_FRONTEND_PLACEHOLDER_TITLE = "新会话"
_MODEL_KEY = "title-test-model"


async def _init_db() -> None:
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["app.server.chat.persistence.conversations"]},
    )
    await Tortoise.generate_schemas()


def _seed_model_catalog() -> None:
    model_catalog.update_from_gateway_rows(
        [
            {
                "id": _MODEL_KEY,
                "task_type": 1,
                "supports_vision": False,
                "supports_video_input": False,
            }
        ]
    )


@pytest.mark.asyncio
async def test_generate_title_applies_when_conversation_has_frontend_placeholder() -> None:
    """前端占位标题会话在首轮后应被总结标题替换"""
    await _init_db()
    _seed_model_catalog()
    try:
        row = await ChatConversations.create(
            user_id=1,
            title=_FRONTEND_PLACEHOLDER_TITLE,
            default_model=_MODEL_KEY,
            status=ChatConversationStatus.ACTIVE,
        )
        gateway_response = {
            "code": 0,
            "choices": [{"message": {"content": '{"title": "每五分钟喝水提醒"}'}}],
        }
        with patch(
            "app.agent.chat.conversation_title.gateway_client.openai_chat_completion",
            new=AsyncMock(return_value=gateway_response),
        ):
            result = await generate_conversation_title_via_llm(
                user_id=1,
                conversation_id=int(row.id),
                user_content="帮我创建一个定时任务，每5分钟提醒我喝水",
                turn_asset_ids=(),
                model_key=_MODEL_KEY,
            )

        assert result.applied is True
        assert result.title == "每五分钟喝水提醒"
        refreshed = await ChatConversations.get(id=row.id)
        assert refreshed.title == "每五分钟喝水提醒"
    finally:
        model_catalog.clear()
        await Tortoise.close_connections()
