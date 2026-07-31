from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.server.api.v1.router import api_router
from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.exceptions.handlers import register_exception_handlers
from app.server.infra.config import settings
from app.server.infra.database import db
from app.server.infra.security import create_access_token
from app.server.middleware import JWTAuthMiddleware
from app.server.workshop.persistence.models import WorkshopProjects


def _app() -> FastAPI:
    """内部辅助：app"""
    application = FastAPI()
    application.add_middleware(JWTAuthMiddleware, whitelist=set(), whitelist_prefixes=[])
    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return application


async def _ensure_selected_expert_column() -> None:
    """确保 selected_expert 列存在"""
    from tortoise import Tortoise

    await Tortoise.get_connection("default").execute_query(
        "ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS selected_expert_key VARCHAR(128)"
    )


@asynccontextmanager
async def _client(*, user_id: int = 1) -> AsyncIterator[tuple[AsyncClient, int]]:
    """构造测试 HTTP 客户端"""
    await db.connect()
    await _ensure_selected_expert_column()
    suffix = uuid4().hex
    chat = await ChatConversations.create(
        user_id=user_id,
        title=f"expert-select-{suffix}",
        default_model="test",
        status=ChatConversationStatus.ACTIVE,
    )
    chat_id = int(chat.id)
    transport = ASGITransport(app=_app())
    token = create_access_token(subject=user_id)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            yield client, chat_id
        finally:
            await WorkshopProjects.filter(group_chat_id=chat_id, user_id=user_id).delete()
            await ChatConversations.filter(id=chat_id, user_id=user_id).delete()
            await db.disconnect()


@pytest.mark.asyncio
async def test_select_switch_remove_expert_on_conversation() -> None:
    """选择、切换、清除会话专家（业务专家）"""
    async with _client() as (client, chat_id):
        set_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/set",
            json={"conversation_id": chat_id, "expert_key": "ecom_market_competitor_advisor"},
        )
        assert set_resp.status_code == 200
        body = set_resp.json()["data"]
        assert body["conversation_id"] == chat_id
        assert body["expert_key"] == "ecom_market_competitor_advisor"
        assert body["name"] == "市场与竞品研究"
        assert body["avatar_id"]

        get_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/get",
            json={"conversation_id": chat_id},
        )
        assert get_resp.json()["data"]["expert_key"] == "ecom_market_competitor_advisor"

        switch_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/set",
            json={"conversation_id": chat_id, "expert_key": "ecom_listing_planner_executor"},
        )
        assert switch_resp.json()["data"]["expert_key"] == "ecom_listing_planner_executor"
        assert switch_resp.json()["data"]["name"] == "商品策划与文案"

        clear_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/clear",
            json={"conversation_id": chat_id},
        )
        assert clear_resp.json()["data"]["expert_key"] is None


@pytest.mark.asyncio
async def test_team_select_and_upgrade_are_rejected() -> None:
    """团队选择与团队升级均不可用"""
    async with _client() as (client, chat_id):
        teams_resp = await client.post("/api/v1/workshop/expert/teams", json={})
        assert teams_resp.status_code == 200
        assert teams_resp.json()["data"]["items"] == []

        host_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/set",
            json={"conversation_id": chat_id, "expert_key": "host"},
        )
        assert host_resp.status_code >= 400

        upgrade_resp = await client.post(
            "/api/v1/workshop/upgrade/from-team",
            json={"conversation_id": chat_id, "team_key": "general_collab_team"},
        )
        assert upgrade_resp.status_code >= 400
        assert "团队" in (upgrade_resp.json().get("msg") or "")


@pytest.mark.asyncio
async def test_upgrade_from_expert_clears_selected_expert_to_host_default() -> None:
    """单专家升级后 selected_expert 清空，默认发给项目助手"""
    async with _client() as (client, chat_id):
        await client.post(
            "/api/v1/chat/conversation/selected-expert/set",
            json={"conversation_id": chat_id, "expert_key": "ecom_market_competitor_advisor"},
        )
        upgrade_resp = await client.post(
            "/api/v1/workshop/upgrade/from-expert",
            json={
                "conversation_id": chat_id,
                "expert_key": "ecom_market_competitor_advisor",
                "project_name": "升级后默认助手",
                "carried_message_count": 0,
            },
        )
        assert upgrade_resp.status_code == 200, upgrade_resp.text
        assert upgrade_resp.json()["data"].get("pending_task_proposal") is None
        get_resp = await client.post(
            "/api/v1/chat/conversation/selected-expert/get",
            json={"conversation_id": chat_id},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["expert_key"] is None
