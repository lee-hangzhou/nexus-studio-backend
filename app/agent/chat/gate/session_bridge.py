"""Session bridge — import cookies from user's logged-in browser."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import TypeAdapter

from app.agent.chat.gate import vault
from app.server.infra.config import settings
from app.server.infra.logger import logger

_JSON_OBJECT = TypeAdapter(dict[str, Any])

_memory: dict[str, dict[str, Any]] = {}
_use_memory = False


def use_memory_backend(enabled: bool = True) -> None:
    global _use_memory
    _use_memory = enabled


def clear_memory() -> None:
    _memory.clear()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bridge_key(token_hash: str) -> str:
    return f"chat:bridge:{token_hash}"


def _gate_status_key(gate_id: str) -> str:
    return f"chat:bridge_gate:{gate_id}"


async def create_bridge_token(
    *,
    user_id: int,
    conversation_id: int,
    gate_id: str,
    expected_domain: str,
) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.CHAT_BRIDGE_TTL_SEC)
    record = {
        "token_hash": token_hash,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "gate_id": gate_id,
        "expected_domain": expected_domain,
        "status": "pending",
        "consumed": False,
        "expires_at": expires_at.isoformat(),
    }
    if _use_memory:
        _memory[_bridge_key(token_hash)] = record
        _memory[_gate_status_key(gate_id)] = {"status": "pending", "token_hash": token_hash}
    else:
        from app.server.infra.redis import redis_client

        await redis_client.set(
            _bridge_key(token_hash),
            json.dumps(record, ensure_ascii=False),
            ex=settings.CHAT_BRIDGE_TTL_SEC,
        )
        await redis_client.set(
            _gate_status_key(gate_id),
            json.dumps({"status": "pending", "token_hash": token_hash}, ensure_ascii=False),
            ex=settings.CHAT_BRIDGE_TTL_SEC,
        )
    logger.info(
        "session_bridge_token_created",
        conversation_id=conversation_id,
        gate_id=gate_id,
        token_prefix=token[:8],
        expected_domain=expected_domain,
    )
    return {
        "bridge_token": token,
        "bridge_status": "pending",
        "domain": expected_domain,
        "expires_at": expires_at.isoformat(),
    }


async def get_bridge_status_by_gate(gate_id: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory.get(_gate_status_key(gate_id))
        return dict(data) if data else None
    from app.server.infra.redis import redis_client

    raw = await redis_client.get(_gate_status_key(gate_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    token_hash = str(data.get("token_hash") or "")
    if not token_hash:
        return data
    record = await _get_bridge_record(token_hash)
    if record is None:
        return {"status": "expired"}
    status = record.get("status")
    if not isinstance(status, str) or status not in {"pending", "imported", "expired"}:
        raise ValueError("bridge status violates contract")
    expected_domain = record.get("expected_domain")
    if not isinstance(expected_domain, str) or not expected_domain:
        raise ValueError("bridge record missing expected_domain")
    return {"status": status, "domain": expected_domain}


async def _get_bridge_record(token_hash: str) -> dict[str, Any] | None:
    if _use_memory:
        data = _memory.get(_bridge_key(token_hash))
        return dict(data) if data else None
    from app.server.infra.redis import redis_client

    raw = await redis_client.get(_bridge_key(token_hash))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("bridge record is not valid JSON") from exc
    return _JSON_OBJECT.validate_python(data)


async def import_bridge_cookies(
    *,
    bridge_token: str,
    cookies: list[dict[str, Any]],
    page_url: str,
) -> dict[str, Any]:
    token_hash = _hash_token(bridge_token)
    record = await _get_bridge_record(token_hash)
    if record is None:
        raise ValueError("bridge token not found or expired")
    if record.get("consumed"):
        raise ValueError("bridge token already consumed")
    expected = str(record.get("expected_domain") or "")
    parsed = urlparse(page_url)
    host = (parsed.hostname or "").lower()
    if expected and not (host == expected or host.endswith(f".{expected}")):
        raise PermissionError(f"domain mismatch: expected {expected}, got {host}")

    gate_id = str(record.get("gate_id") or "")
    await vault.put(gate_id, {"_bridge_cookies": json.dumps(cookies)})
    record["status"] = "imported"
    record["consumed"] = True
    if _use_memory:
        _memory[_bridge_key(token_hash)] = record
        _memory[_gate_status_key(gate_id)] = {"status": "imported", "token_hash": token_hash}
    else:
        from app.server.infra.redis import redis_client

        await redis_client.set(
            _bridge_key(token_hash),
            json.dumps(record, ensure_ascii=False),
            ex=settings.CHAT_BRIDGE_TTL_SEC,
        )
        await redis_client.set(
            _gate_status_key(gate_id),
            json.dumps({"status": "imported", "token_hash": token_hash}, ensure_ascii=False),
            ex=settings.CHAT_BRIDGE_TTL_SEC,
        )
    logger.info(
        "session_bridge_imported",
        gate_id=gate_id,
        cookie_count=len(cookies),
        domain=host,
    )
    return {"status": "imported", "cookie_count": len(cookies)}
