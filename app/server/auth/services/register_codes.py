import json
import secrets
from typing import Any

from app.server.infra.config import settings
from app.server.infra.redis import redis_client
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

REGISTER_CODE_PREFIX = "auth:register_code:"
COOLDOWN_PREFIX = "auth:register_code:cooldown:"
MAX_VERIFY_ATTEMPTS = 5


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _code_key(email: str) -> str:
    return f"{REGISTER_CODE_PREFIX}{normalize_email(email)}"


def _cooldown_key(email: str) -> str:
    return f"{COOLDOWN_PREFIX}{normalize_email(email)}"


def generate_code() -> str:
    length = settings.REGISTER_CODE_LENGTH
    return "".join(secrets.choice("0123456789") for _ in range(length))


async def issue_register_code(email: str) -> str:
    normalized = normalize_email(email)

    if await redis_client.exists(_cooldown_key(normalized)):
        raise AppError(ErrorCode.REGISTER_CODE_RATE_LIMITED)

    code = generate_code()
    payload = json.dumps({"code": code, "attempts": 0})
    ttl = settings.REGISTER_CODE_EXPIRE_MINUTES * 60
    await redis_client.set(_code_key(normalized), payload, ex=ttl)
    await redis_client.set(_cooldown_key(normalized), "1", ex=settings.REGISTER_CODE_RESEND_SECONDS)
    return code


async def verify_and_consume_register_code(email: str, code: str) -> None:
    normalized = normalize_email(email)
    key = _code_key(normalized)
    raw = await redis_client.get(key)
    if raw is None:
        raise AppError(ErrorCode.INVALID_VERIFICATION_CODE)

    try:
        stored: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        await redis_client.delete(key)
        raise AppError(ErrorCode.INVALID_VERIFICATION_CODE) from exc

    attempts = int(stored.get("attempts", 0))
    if attempts >= MAX_VERIFY_ATTEMPTS:
        await redis_client.delete(key)
        raise AppError(ErrorCode.INVALID_VERIFICATION_CODE)

    if stored.get("code") != code.strip():
        stored["attempts"] = attempts + 1
        ttl = await redis_client.ttl(key)
        if ttl > 0:
            await redis_client.set(key, json.dumps(stored), ex=ttl)
        else:
            await redis_client.delete(key)
        raise AppError(ErrorCode.INVALID_VERIFICATION_CODE)

    await redis_client.delete(key)
