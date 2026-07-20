"""OTP flow metadata per conversation — otp_flow_id lifecycle."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.config import settings

_memory: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory.clear()


def _flow_key(conversation_id: int, otp_flow_id: str) -> str:
    return f"chat:otp_flow:{conversation_id}:{otp_flow_id}"


def _send_key(conversation_id: int, otp_flow_id: str, send_selector: str) -> str:
    import hashlib

    digest = hashlib.sha256(send_selector.encode()).hexdigest()[:16]
    return f"chat:otp_send:{conversation_id}:{otp_flow_id}:{digest}"


async def start_otp_flow(conversation_id: int) -> str:
    otp_flow_id = uuid.uuid4().hex
    payload = {"conversation_id": conversation_id, "status": "phone_pending"}
    if _use_memory:
        _memory[_flow_key(conversation_id, otp_flow_id)] = payload
        return otp_flow_id
    from app.core.redis import redis_client

    await redis_client.set(
        _flow_key(conversation_id, otp_flow_id),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )
    return otp_flow_id


async def get_otp_flow(conversation_id: int, otp_flow_id: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory.get(_flow_key(conversation_id, otp_flow_id))
        return dict(data) if data else None
    from app.core.redis import redis_client

    raw = await redis_client.get(_flow_key(conversation_id, otp_flow_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def set_otp_send_selector(
    conversation_id: int,
    otp_flow_id: str,
    send_selector: str,
) -> None:
    flow = await get_otp_flow(conversation_id, otp_flow_id)
    if flow is None:
        raise KeyError("otp flow not found")
    flow["send_selector"] = send_selector
    flow["status"] = "send_registered"
    if _use_memory:
        _memory[_flow_key(conversation_id, otp_flow_id)] = flow
        return
    from app.core.redis import redis_client

    await redis_client.set(
        _flow_key(conversation_id, otp_flow_id),
        json.dumps(flow, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


async def mark_otp_sent(conversation_id: int, otp_flow_id: str, send_selector: str) -> bool:
    """Returns False if already sent (idempotent)."""
    if _use_memory:
        key = _send_key(conversation_id, otp_flow_id, send_selector)
        if key in _memory:
            return False
        _memory[key] = {"sent": True}
        return True
    from app.core.redis import redis_client

    key = _send_key(conversation_id, otp_flow_id, send_selector)
    ok = await redis_client.set(key, "1", ex=settings.CHAT_GATE_TTL_SEC, nx=True)
    return bool(ok)


async def mark_phone_submitted(conversation_id: int, otp_flow_id: str) -> None:
    flow = await get_otp_flow(conversation_id, otp_flow_id)
    if flow is None:
        return
    flow["status"] = "phone_submitted"
    if _use_memory:
        _memory[_flow_key(conversation_id, otp_flow_id)] = flow
        return
    from app.core.redis import redis_client

    await redis_client.set(
        _flow_key(conversation_id, otp_flow_id),
        json.dumps(flow, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )
