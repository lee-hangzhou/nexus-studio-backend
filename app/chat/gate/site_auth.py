"""Conversation+domain scoped auth facts (Redis)."""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.config import settings

_memory_store: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory_store.clear()


def _key(conversation_id: int, domain: str) -> str:
    return f"chat:site_auth:{conversation_id}:{domain}"


async def get_site_auth(conversation_id: int, domain: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory_store.get(_key(conversation_id, domain))
        return dict(data) if data is not None else None
    from app.core.redis import redis_client

    raw = await redis_client.get(_key(conversation_id, domain))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("site auth state is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("site auth state must be an object")
    return data


async def _set_site_auth(conversation_id: int, domain: str, payload: dict[str, Any]) -> None:
    if _use_memory:
        _memory_store[_key(conversation_id, domain)] = payload
        return
    from app.core.redis import redis_client

    await redis_client.set(
        _key(conversation_id, domain),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


async def merge_site_auth(conversation_id: int, domain: str, **fields: Any) -> dict[str, Any]:
    current = await get_site_auth(conversation_id, domain) or {}
    current.update(fields)
    current["domain"] = domain
    current["updated_at"] = time.time()
    await _set_site_auth(conversation_id, domain, current)
    return current


async def mark_login_method_selected(
    *,
    conversation_id: int,
    domain: str,
    choice_id: str,
    target_gate: str,
) -> dict[str, Any]:
    return await merge_site_auth(
        conversation_id,
        domain,
        login_method_selected=True,
        choice_id=choice_id,
        target_gate=target_gate,
    )


async def mark_credentials_submitted(*, conversation_id: int, domain: str) -> dict[str, Any]:
    """Workflow fact: user submitted credentials gate and mechanism dispatched fill/click."""
    return await merge_site_auth(
        conversation_id,
        domain,
        credentials_submitted=True,
    )


async def mark_phone_otp_flow_started(*, conversation_id: int, domain: str) -> dict[str, Any]:
    return await merge_site_auth(
        conversation_id,
        domain,
        phone_otp_flow_started=True,
    )


async def is_login_method_selected(conversation_id: int, domain: str) -> bool:
    state = await get_site_auth(conversation_id, domain)
    return bool(state and state.get("login_method_selected"))
