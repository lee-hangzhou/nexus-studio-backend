"""Redis-backed gate metadata for gate/asset screenshots."""

from __future__ import annotations

import json
from typing import Any, Literal

from app.core.config import settings

AssetKind = Literal["qr", "captcha", "challenge"]

_memory_store: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory_store.clear()


def _key(gate_id: str) -> str:
    return f"chat:gate_meta:{gate_id}"


async def set_gate_meta(gate_id: str, **fields: Any) -> None:
    existing = await get_gate_meta(gate_id) or {}
    payload = {**existing, **{k: v for k, v in fields.items() if v is not None}}
    if _use_memory:
        _memory_store[gate_id] = payload
        return
    from app.core.redis import redis_client

    await redis_client.set(
        _key(gate_id),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


async def get_gate_meta(gate_id: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory_store.get(gate_id)
        return dict(data) if data is not None else None
    from app.core.redis import redis_client

    raw = await redis_client.get(_key(gate_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("gate metadata is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("gate metadata must be an object")
    return data


async def clear_gate_meta(gate_id: str) -> None:
    if _use_memory:
        _memory_store.pop(gate_id, None)
        return
    from app.core.redis import redis_client

    await redis_client.delete(_key(gate_id))
