"""Short-lived gate secrets (Redis in production; in-memory for unit tests)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.server.infra.config import settings
from app.server.infra.logger import logger

_memory_store: dict[str, dict[str, Any]] = {}
_memory_handles: dict[str, str] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory_store.clear()
    _memory_handles.clear()


def _secret_key(handle: str) -> str:
    return f"chat:gate_secret:{handle}"


def _gate_handles_key(gate_id: str) -> str:
    return f"chat:gate_handles:{gate_id}"


async def create_secret(gate_id: str, field_name: str, value: str) -> str:
    handle = f"{gate_id}:{field_name}:{uuid.uuid4().hex[:8]}"
    if _use_memory:
        _memory_handles[handle] = value
        existing = _memory_store.get(_gate_handles_key(gate_id), {})
        existing[field_name] = handle
        _memory_store[_gate_handles_key(gate_id)] = existing
        return handle
    from app.server.infra.redis import redis_client

    await redis_client.set(
        _secret_key(handle),
        value,
        ex=settings.CHAT_GATE_TTL_SEC,
    )
    raw = await redis_client.get(_gate_handles_key(gate_id))
    mapping: dict[str, str] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("gate vault metadata is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("gate vault metadata must be an object")
        mapping = {str(k): str(v) for k, v in parsed.items()}
    mapping[field_name] = handle
    await redis_client.set(
        _gate_handles_key(gate_id),
        json.dumps(mapping, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )
    logger.info("vault_secret_created", gate_id=gate_id, field=field_name, handle_prefix=handle[:16])
    return handle


async def consume_secret(handle: str) -> str | None:
    if _use_memory:
        return _memory_handles.pop(handle, None)
    from app.server.infra.redis import redis_client

    key = _secret_key(handle)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    await redis_client.delete(key)
    logger.info("vault_secret_consumed", handle_prefix=handle[:16])
    return str(raw)


async def peek_meta(gate_id: str) -> dict[str, str]:
    """Return field_name -> handle without consuming."""
    if _use_memory:
        data = _memory_store.get(_gate_handles_key(gate_id), {})
        return dict(data) if isinstance(data, dict) else {}
    from app.server.infra.redis import redis_client

    raw = await redis_client.get(_gate_handles_key(gate_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("gate vault metadata is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("gate vault metadata must be an object")
    return {str(k): str(v) for k, v in data.items()}


async def put(gate_id: str, fields: dict[str, Any]) -> None:
    """Store user-submitted fields as consumable secrets."""
    for name, value in fields.items():
        if value is None:
            continue
        await create_secret(gate_id, str(name), str(value))


async def take(gate_id: str) -> dict[str, Any] | None:
    """Consume all secrets for a gate and return field values."""
    handles = await peek_meta(gate_id)
    if not handles:
        return None
    out: dict[str, Any] = {}
    for field_name, handle in handles.items():
        value = await consume_secret(handle)
        if value is not None:
            out[field_name] = value
    if _use_memory:
        _memory_store.pop(_gate_handles_key(gate_id), None)
    else:
        from app.server.infra.redis import redis_client

        await redis_client.delete(_gate_handles_key(gate_id))
    if not out:
        return None
    return out
