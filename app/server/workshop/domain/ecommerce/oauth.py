from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTHORIZE_URL = "https://oauth.taobao.com/authorize"
TOKEN_URL = "https://oauth.taobao.com/token"

CODE_TTL = timedelta(minutes=30)


class TaobaoAppType(str, Enum):
    SELF_DEV = "self_dev"
    SUBSCRIPTION_ISV = "subscription_isv"


class ShopConnectionStatus(str, Enum):
    CONNECTED = "connected"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    DISCONNECTED = "disconnected"


class OAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        """初始化"""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class OAuthClientConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    app_type: TaobaoAppType = TaobaoAppType.SELF_DEV
    refresh_enabled: bool = False  # default off; only for verified refreshable types


@dataclass
class StoredCredential:
    shop_connection_id: str
    access_token: str
    expires_in: int
    expires_at: datetime
    refresh_token: str | None = None
    re_expires_in: int | None = None
    taobao_user_nick: str | None = None
    taobao_open_uid: str | None = None


@dataclass
class ShopConnectionView:
    """对外安全的店铺连接视图；不含 token 或密钥"""

    shop_connection_id: str
    status: ShopConnectionStatus
    app_type: TaobaoAppType
    expires_at: datetime | None = None
    semantic_permissions: tuple[str, ...] = ()


class CredentialStore(Protocol):
    def save(self, credential: StoredCredential) -> None:
        """持久化店铺凭据"""
        ...

    def get(self, shop_connection_id: str) -> StoredCredential | None:
        """按连接 id 读取凭据"""
        ...


@dataclass
class InMemoryCredentialStore:
    _items: dict[str, StoredCredential] = field(default_factory=dict)

    def save(self, credential: StoredCredential) -> None:
        """持久化店铺凭据"""
        self._items[credential.shop_connection_id] = credential

    def get(self, shop_connection_id: str) -> StoredCredential | None:
        """按连接 id 读取凭据"""
        return self._items.get(shop_connection_id)


@dataclass
class PendingAuth:
    state: str
    shop_connection_id: str
    created_at: datetime
    code_used: set[str] = field(default_factory=set)


@dataclass
class TaobaoOAuthService:
    config: OAuthClientConfig
    store: CredentialStore
    http_client: httpx.Client
    _pending: dict[str, PendingAuth] = field(default_factory=dict)
    _refresh_calls: int = 0

    def build_authorize_url(self, *, shop_connection_id: str, state: str | None = None) -> str:
        """构造授权跳转 URL"""
        csrf = state or secrets.token_urlsafe(24)
        self._pending[csrf] = PendingAuth(
            state=csrf,
            shop_connection_id=shop_connection_id,
            created_at=datetime.now(timezone.utc),
        )
        query = urlencode(
            {
                "client_id": self.config.client_id,
                "response_type": "code",
                "redirect_uri": self.config.redirect_uri,
                "state": csrf,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def handle_callback(
        self,
        *,
        code: str,
        state: str,
        now: datetime | None = None,
    ) -> ShopConnectionView:
        """处理 OAuth 回调换票"""
        now = now or datetime.now(timezone.utc)
        pending = self._pending.get(state)
        if pending is None or pending.state != state:
            raise OAuthError("TAOBAO_OAUTH_STATE_MISMATCH", "oauth state mismatch")
        if not code or not code.strip():
            raise OAuthError("TAOBAO_OAUTH_CALLBACK_INVALID", "code required")
        if code in pending.code_used:
            raise OAuthError("TAOBAO_OAUTH_CODE_REUSED", "authorization code already used")
        if now - pending.created_at > CODE_TTL:
            raise OAuthError("TAOBAO_OAUTH_CODE_EXPIRED", "authorization code expired")

        token_payload = self._exchange_code(code=code, state=state)
        pending.code_used.add(code)

        access_token = token_payload.get("access_token")
        if not access_token:
            raise OAuthError("TAOBAO_OAUTH_TOKEN_INVALID", "token response missing access_token")
        expires_in = int(token_payload.get("expires_in") or 0)
        if expires_in <= 0:
            raise OAuthError("TAOBAO_OAUTH_TOKEN_INVALID", "token response missing expires_in")

        credential = StoredCredential(
            shop_connection_id=pending.shop_connection_id,
            access_token=str(access_token),
            expires_in=expires_in,
            expires_at=now + timedelta(seconds=expires_in),
            refresh_token=token_payload.get("refresh_token"),
            re_expires_in=_optional_int(token_payload.get("re_expires_in")),
            taobao_user_nick=token_payload.get("taobao_user_nick"),
            taobao_open_uid=token_payload.get("taobao_open_uid"),
        )
        self.store.save(credential)
        return self.public_view(pending.shop_connection_id, now=now)

    def _exchange_code(self, *, code: str, state: str) -> dict[str, Any]:
        """用授权码换取 token"""
        try:
            response = self.http_client.post(
                TOKEN_URL,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.config.redirect_uri,
                    "state": state,
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError("TAOBAO_OAUTH_HTTP_ERROR", str(exc)) from exc
        if response.status_code >= 400:
            raise OAuthError("TAOBAO_OAUTH_PROVIDER_ERROR", f"token http {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OAuthError("TAOBAO_OAUTH_TOKEN_INVALID", "token response not an object")
        result: dict[str, Any] = dict(payload)
        if result.get("error") or result.get("error_description"):
            raise OAuthError(
                "TAOBAO_OAUTH_PROVIDER_ERROR",
                str(result.get("error_description") or result.get("error")),
            )
        return result

    def public_view(
        self, shop_connection_id: str, *, now: datetime | None = None
    ) -> ShopConnectionView:
        """组装对外连接视图"""
        now = now or datetime.now(timezone.utc)
        cred = self.store.get(shop_connection_id)
        if cred is None:
            return ShopConnectionView(
                shop_connection_id=shop_connection_id,
                status=ShopConnectionStatus.DISCONNECTED,
                app_type=self.config.app_type,
            )
        if cred.expires_at <= now:
            return ShopConnectionView(
                shop_connection_id=shop_connection_id,
                status=ShopConnectionStatus.REAUTH_REQUIRED,
                app_type=self.config.app_type,
                expires_at=cred.expires_at,
            )
        return ShopConnectionView(
            shop_connection_id=shop_connection_id,
            status=ShopConnectionStatus.CONNECTED,
            app_type=self.config.app_type,
            expires_at=cred.expires_at,
        )

    def try_refresh(
        self, shop_connection_id: str, *, now: datetime | None = None
    ) -> ShopConnectionView:
        """仅在应用类型策略明确允许时刷新；自研应用永不自动刷新"""
        now = now or datetime.now(timezone.utc)
        if self.config.app_type == TaobaoAppType.SELF_DEV or not self.config.refresh_enabled:
            view = self.public_view(shop_connection_id, now=now)
            if view.status == ShopConnectionStatus.CONNECTED:
                return view
            return ShopConnectionView(
                shop_connection_id=shop_connection_id,
                status=ShopConnectionStatus.REAUTH_REQUIRED,
                app_type=self.config.app_type,
                expires_at=view.expires_at,
            )

        cred = self.store.get(shop_connection_id)
        if cred is None or not cred.refresh_token:
            raise OAuthError("TAOBAO_OAUTH_REFRESH_UNAVAILABLE", "no refresh_token")
        self._refresh_calls += 1
        response = self.http_client.post(
            TOKEN_URL,
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": cred.refresh_token,
            },
        )
        payload = response.json()
        access_token = payload["access_token"]
        expires_in = int(payload["expires_in"])
        now = datetime.now(timezone.utc)
        updated = StoredCredential(
            shop_connection_id=shop_connection_id,
            access_token=str(access_token),
            expires_in=expires_in,
            expires_at=now + timedelta(seconds=expires_in),
            refresh_token=payload.get("refresh_token") or cred.refresh_token,
            re_expires_in=_optional_int(payload.get("re_expires_in")),
            taobao_user_nick=cred.taobao_user_nick,
            taobao_open_uid=cred.taobao_open_uid,
        )
        self.store.save(updated)
        return self.public_view(shop_connection_id)

    def connection_to_public_dict(self, view: ShopConnectionView) -> dict[str, Any]:
        """连接视图转公开 dict"""
        return {
            "shop_connection_id": view.shop_connection_id,
            "status": view.status.value,
            "app_type": view.app_type.value,
            "expires_at": view.expires_at.isoformat() if view.expires_at else None,
        }


def parse_authorize_query(url: str) -> dict[str, str]:
    """解析授权回调 query"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return {k: v[0] for k, v in qs.items()}


def _optional_int(value: Any) -> int | None:
    """可选整数字段解析"""
    if value is None or value == "":
        return None
    return int(value)


def redact_secrets_from_log_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """记录日志前剥离 token 与密钥字段"""
    forbidden = {
        "access_token",
        "refresh_token",
        "client_secret",
        "session",
        "code",
    }
    return {k: ("***" if k in forbidden else v) for k, v in dict(payload).items()}
