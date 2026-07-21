"""Redis-backed gate pending state per conversation."""

from __future__ import annotations

import json
from typing import Any, Literal

from app.chat.gate.fields import normalize_field_defs
from app.chat.gate.public_payload import strip_public_gate_payload
from app.core.config import settings
from app.core.redis import redis_client

GatePendingStatus = Literal["pending", "submitted", "resuming", "expired", "cancelled"]


def _key(conversation_id: int) -> str:
    return f"chat:gate_pending:{conversation_id}"


async def set_gate_pending(
    conversation_id: int,
    *,
    turn_id: str,
    gate_id: str,
    gate_type: str,
    model_key: str,
    prompt: str,
    fields: list[dict[str, Any]],
    choices: list[dict[str, Any]],
    phase: str | None = None,
    assets: dict[str, Any],
    status: GatePendingStatus = "pending",
    domain: str | None = None,
) -> None:
    payload: dict[str, Any] = strip_public_gate_payload(
        {
            "turn_id": turn_id,
            "gate_id": gate_id,
            "gate_type": gate_type,
            "model_key": model_key,
            "prompt": prompt,
            "fields": normalize_field_defs(fields),
            "choices": list(choices),
            "assets": dict(assets),
            "status": status,
        }
    )
    if phase is not None:
        payload["phase"] = phase
    if domain is not None:
        payload["domain"] = domain
    await redis_client.set(
        _key(conversation_id),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


async def update_gate_pending_status(
    conversation_id: int,
    status: GatePendingStatus,
) -> None:
    pending = await get_gate_pending(conversation_id)
    if not pending:
        return
    pending["status"] = status
    await redis_client.set(
        _key(conversation_id),
        json.dumps(pending, ensure_ascii=False),
        ex=settings.CHAT_GATE_TTL_SEC,
    )


def _parse_gate_pending_raw(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("gate pending state is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("gate pending state must be an object")
    parsed = strip_public_gate_payload(data)
    required_strings = ("turn_id", "gate_id", "gate_type", "model_key", "prompt", "status")
    if any(not isinstance(parsed.get(key), str) or not parsed[key] for key in required_strings):
        raise ValueError("gate pending state is missing required fields")
    if parsed["status"] not in {"pending", "submitted", "resuming", "expired", "cancelled"}:
        raise ValueError("gate pending state has unknown status")
    raw_fields = parsed.get("fields")
    raw_choices = parsed.get("choices")
    raw_assets = parsed.get("assets")
    if not isinstance(raw_fields, list) or not isinstance(raw_choices, list) or not isinstance(raw_assets, dict):
        raise ValueError("gate pending state has invalid collections")
    parsed["fields"] = normalize_field_defs(raw_fields)
    return parsed


async def get_gate_pending(conversation_id: int) -> dict[str, Any] | None:
    raw = await redis_client.get(_key(conversation_id))
    return _parse_gate_pending_raw(raw)


async def get_gate_pending_many(conversation_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not conversation_ids:
        return {}
    keys = [_key(conversation_id) for conversation_id in conversation_ids]
    raws = await redis_client.mget(*keys)
    out: dict[int, dict[str, Any]] = {}
    for conversation_id, raw in zip(conversation_ids, raws, strict=True):
        parsed = _parse_gate_pending_raw(raw)
        if parsed is not None:
            out[conversation_id] = parsed
    return out


async def clear_gate_pending(conversation_id: int) -> None:
    await redis_client.delete(_key(conversation_id))
