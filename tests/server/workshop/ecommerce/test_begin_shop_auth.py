from __future__ import annotations

import pytest

from app.server.infra.config import settings
from app.server.workshop.services.workshop_project_service import (
    WorkshopProjectError,
    WorkshopProjectService,
)


@pytest.mark.asyncio
async def test_begin_shop_auth_fails_closed_when_taobao_app_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置淘宝应用时 begin_shop_auth 明确失败，不返回空 authorize_url"""
    monkeypatch.setattr(settings, "TAOBAO_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "TAOBAO_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "TAOBAO_OAUTH_REDIRECT_URI", "")
    import app.server.workshop.services.workshop_project_service as svc_mod

    monkeypatch.setattr(svc_mod, "_taobao_oauth_service", None)

    class _Repo:
        async def get_project_brief(self, **kwargs):
            return {}

    service = WorkshopProjectService(_Repo())  # type: ignore[arg-type]

    async def _get_project(**kwargs):
        return object()

    monkeypatch.setattr(service, "get_project", _get_project)

    with pytest.raises(WorkshopProjectError, match="当前环境未配置淘宝应用"):
        await service.begin_shop_auth(project_id="wp_1", user_id=1)


@pytest.mark.asyncio
async def test_begin_shop_auth_returns_authorize_url_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已配置淘宝应用时返回真实 authorize_url"""
    monkeypatch.setattr(settings, "TAOBAO_OAUTH_CLIENT_ID", "app_key_test")
    monkeypatch.setattr(settings, "TAOBAO_OAUTH_CLIENT_SECRET", "secret_test")
    monkeypatch.setattr(
        settings, "TAOBAO_OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback"
    )
    import app.server.workshop.services.workshop_project_service as svc_mod

    monkeypatch.setattr(svc_mod, "_taobao_oauth_service", None)

    class _Repo:
        def __init__(self) -> None:
            self.brief: dict = {}

        async def get_project_brief(self, **kwargs):
            return dict(self.brief)

        async def update_project_brief(self, *, project_id, user_id, brief):
            self.brief = dict(brief)

    repo = _Repo()
    service = WorkshopProjectService(repo)  # type: ignore[arg-type]

    async def _get_project(**kwargs):
        return object()

    monkeypatch.setattr(service, "get_project", _get_project)

    result = await service.begin_shop_auth(project_id="wp_1", user_id=1)
    assert result.authorize_url
    assert result.authorize_url.startswith("https://oauth.taobao.com/authorize?")
    assert "client_id=app_key_test" in result.authorize_url
    assert result.status == "authorizing"
