"""Turn-scoped auth method selection state (Redis)."""

from __future__ import annotations

import json
import time
from typing import Any

from app.server.infra.config import settings

_memory_store: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory_store.clear()


def _key(conversation_id: int, turn_id: str) -> str:
    return f"chat:turn_auth:{conversation_id}:{turn_id}"


async def is_method_selected(conversation_id: int, turn_id: str) -> bool:
    state = await get_turn_auth(conversation_id, turn_id)
    return bool(state and state.get("method_selected"))


async def get_turn_auth(conversation_id: int, turn_id: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory_store.get(_key(conversation_id, turn_id))
        return dict(data) if data is not None else None
    from app.server.infra.redis import redis_client

    raw = await redis_client.get(_key(conversation_id, turn_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("turn auth state is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("turn auth state must be an object")
    return data


async def set_method_selected(
    *,
    conversation_id: int,
    turn_id: str,
    choice_id: str,
    target_gate: str,
) -> None:
    payload = {
        "method_selected": True,
        "choice_id": choice_id,
        "target_gate": target_gate,
        "selected_at": time.time(),
    }
    if _use_memory:
        _memory_store[_key(conversation_id, turn_id)] = payload
        return
    from app.server.infra.redis import redis_client

    await redis_client.set(
        _key(conversation_id, turn_id),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


async def clear_turn_auth(conversation_id: int, turn_id: str) -> None:
    if _use_memory:
        _memory_store.pop(_key(conversation_id, turn_id), None)
        return
    from app.server.infra.redis import redis_client

    await redis_client.delete(_key(conversation_id, turn_id))
