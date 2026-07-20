"""Redis snapshot of turn observation context for gate resume continuity."""

from __future__ import annotations

import json
import time
from typing import Any

from app.contracts.metadata import TurnContextMetadata
from app.core.config import settings

_memory_store: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory_store.clear()


def _key(conversation_id: int, turn_id: str) -> str:
    return f"chat:turn_observation:{conversation_id}:{turn_id}"


async def save_turn_observation_snapshot(
    *,
    conversation_id: int,
    turn_id: str,
    model_key: str,
    turn_context: TurnContextMetadata,
    limits: dict[str, int],
    attachments: list[str],
) -> None:
    payload = {
        "model_key": model_key,
        "turn_context": turn_context.model_dump(mode="json"),
        "limits": limits,
        "attachments": attachments,
        "saved_at": time.time(),
    }
    if _use_memory:
        _memory_store[_key(conversation_id, turn_id)] = payload
        return
    from app.core.redis import redis_client

    await redis_client.set(
        _key(conversation_id, turn_id),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_TURN_WALL_CLOCK_SEC + settings.CHAT_GATE_TTL_SEC,
    )


async def load_turn_observation_snapshot(
    conversation_id: int,
    turn_id: str,
) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory_store.get(_key(conversation_id, turn_id))
        return dict(data) if data is not None else None
    from app.core.redis import redis_client

    raw = await redis_client.get(_key(conversation_id, turn_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def clear_turn_observation_snapshot(conversation_id: int, turn_id: str) -> None:
    if _use_memory:
        _memory_store.pop(_key(conversation_id, turn_id), None)
        return
    from app.core.redis import redis_client

    await redis_client.delete(_key(conversation_id, turn_id))
