from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import pytest

from app.server.workshop.domain.ecommerce.oauth import (
    AUTHORIZE_URL,
    TOKEN_URL,
    InMemoryCredentialStore,
    OAuthClientConfig,
    OAuthError,
    ShopConnectionStatus,
    TaobaoAppType,
    TaobaoOAuthService,
    parse_authorize_query,
    redact_secrets_from_log_payload,
)
from app.server.workshop.domain.ecommerce.taobao_adapter import (
    TaobaoAdapterError,
    TaobaoScopeManifest,
    require_scope_manifest,
)


def _token_transport(payload: dict | None = None, *, status_code: int = 200) -> httpx.MockTransport:
    """构造假 token HTTP 传输"""
    body = payload or {
        "access_token": "tok_secret_value",
        "token_type": "Bearer",
        "expires_in": 86400,
        "refresh_token": "refresh_should_not_leak",
        "re_expires_in": 0,
        "taobao_user_nick": "nick",
        "taobao_open_uid": "uid_1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """假 HTTP 处理器"""
        assert str(request.url).startswith(TOKEN_URL)
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _service(
    *,
    app_type: TaobaoAppType = TaobaoAppType.SELF_DEV,
    refresh_enabled: bool = False,
    transport: httpx.MockTransport | None = None,
) -> TaobaoOAuthService:
    """构造 OAuth 测试服务"""
    client = httpx.Client(transport=transport or _token_transport())
    return TaobaoOAuthService(
        config=OAuthClientConfig(
            client_id="app_key_test",
            client_secret="secret_test_not_for_prod",
            redirect_uri="https://example.test/oauth/callback",
            app_type=app_type,
            refresh_enabled=refresh_enabled,
        ),
        store=InMemoryCredentialStore(),
        http_client=client,
    )


def test_authorize_url_has_required_params() -> None:
    """覆盖 authorize url has required params"""
    svc = _service()
    url = svc.build_authorize_url(shop_connection_id="shop_1", state="csrf_abc")
    assert url.startswith(AUTHORIZE_URL)
    qs = parse_authorize_query(url)
    assert qs["client_id"] == "app_key_test"
    assert qs["response_type"] == "code"
    assert qs["redirect_uri"] == "https://example.test/oauth/callback"
    assert qs["state"] == "csrf_abc"
    assert urlparse(url).netloc == "oauth.taobao.com"


def test_state_mismatch_rejected() -> None:
    """覆盖 state mismatch rejected"""
    svc = _service()
    svc.build_authorize_url(shop_connection_id="shop_1", state="expected")
    with pytest.raises(OAuthError) as exc:
        svc.handle_callback(code="auth_code", state="wrong")
    assert exc.value.code == "TAOBAO_OAUTH_STATE_MISMATCH"


def test_code_exchange_success_stores_token_safely() -> None:
    """覆盖 code exchange success stores token safely"""
    svc = _service()
    svc.build_authorize_url(shop_connection_id="shop_1", state="csrf")
    view = svc.handle_callback(code="auth_code", state="csrf")
    assert view.status == ShopConnectionStatus.CONNECTED
    public = svc.connection_to_public_dict(view)
    assert public["shop_connection_id"] == "shop_1"
    assert "access_token" not in public
    assert "refresh_token" not in public
    assert "client_secret" not in public
    stored = svc.store.get("shop_1")
    assert stored is not None
    assert stored.access_token == "tok_secret_value"
    assert stored.expires_in == 86400


def test_code_single_use() -> None:
    """覆盖 code single use"""
    svc = _service()
    svc.build_authorize_url(shop_connection_id="shop_1", state="csrf")
    svc.handle_callback(code="auth_code", state="csrf")
    with pytest.raises(OAuthError) as exc:
        svc.handle_callback(code="auth_code", state="csrf")
    assert exc.value.code == "TAOBAO_OAUTH_CODE_REUSED"


def test_logs_and_public_contract_do_not_leak_token() -> None:
    """覆盖 logs and public contract do not leak token"""
    redacted = redact_secrets_from_log_payload(
        {
            "access_token": "tok",
            "refresh_token": "ref",
            "client_secret": "sec",
            "session": "sess",
            "shop_connection_id": "shop_1",
        }
    )
    assert redacted["access_token"] == "***"
    assert redacted["refresh_token"] == "***"
    assert redacted["client_secret"] == "***"
    assert redacted["session"] == "***"
    assert redacted["shop_connection_id"] == "shop_1"


def test_self_dev_expired_token_requires_reauth() -> None:
    """覆盖 self dev expired token requires reauth"""
    svc = _service(app_type=TaobaoAppType.SELF_DEV)
    svc.build_authorize_url(shop_connection_id="shop_1", state="csrf")
    now = datetime.now(timezone.utc)
    svc.handle_callback(code="auth_code", state="csrf", now=now)
    later = now + timedelta(seconds=86401)
    view = svc.public_view("shop_1", now=later)
    assert view.status == ShopConnectionStatus.REAUTH_REQUIRED


def test_self_dev_does_not_call_refresh() -> None:
    """覆盖 self dev does not call refresh"""
    refresh_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        """假 HTTP 处理器"""
        body = request.content.decode()
        if "grant_type=refresh_token" in body:
            refresh_hits["n"] += 1
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "expires_in": 60,
                "refresh_token": "r",
            },
        )

    svc = _service(
        app_type=TaobaoAppType.SELF_DEV,
        refresh_enabled=False,
        transport=httpx.MockTransport(handler),
    )
    svc.build_authorize_url(shop_connection_id="shop_1", state="csrf")
    now = datetime.now(timezone.utc)
    svc.handle_callback(code="c", state="csrf", now=now)
    view = svc.try_refresh("shop_1", now=now)
    # still connected within expiry; no refresh HTTP
    assert view.status == ShopConnectionStatus.CONNECTED
    assert refresh_hits["n"] == 0
    assert svc._refresh_calls == 0
    expired = svc.public_view("shop_1", now=now + timedelta(seconds=120))
    assert expired.status == ShopConnectionStatus.REAUTH_REQUIRED
    again = svc.try_refresh("shop_1", now=now + timedelta(seconds=120))
    assert again.status == ShopConnectionStatus.REAUTH_REQUIRED
    assert refresh_hits["n"] == 0
    assert svc._refresh_calls == 0


def test_oauth_provider_error_maps_to_stable_domain_error() -> None:
    """覆盖 oauth provider error maps to stable domain error"""
    def handler(request: httpx.Request) -> httpx.Response:
        """假 HTTP 处理器"""
        return httpx.Response(
            200,
            json={"error": "invalid_code", "error_description": "authorize code expire"},
        )

    svc = _service(transport=httpx.MockTransport(handler))
    svc.build_authorize_url(shop_connection_id="shop_1", state="csrf")
    with pytest.raises(OAuthError) as exc:
        svc.handle_callback(code="bad", state="csrf")
    assert exc.value.code == "TAOBAO_OAUTH_PROVIDER_ERROR"


def test_oauth_callback_requires_scope_manifest_still() -> None:
    """遗留缝：Adapter 边界仍对缺失 scope manifest fail closed"""
    with pytest.raises(TaobaoAdapterError) as exc:
        require_scope_manifest(None)
    assert exc.value.code == "TAOBAO_SCOPE_MANIFEST_MISSING"
    require_scope_manifest(
        TaobaoScopeManifest(
            product_schema_read=True,
            product_write=True,
            product_status_write=True,
        )
    )
