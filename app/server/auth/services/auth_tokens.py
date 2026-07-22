from app.server.infra.config import settings
from app.server.infra.redis import redis_client

REFRESH_KEY_PREFIX = "auth:refresh:"
USER_REFRESH_SET_PREFIX = "auth:user:"
USER_REFRESH_SET_SUFFIX = ":refresh_jtis"
PASSWORD_RESET_KEY_PREFIX = "auth:password_reset:"


def refresh_token_key(jti: str) -> str:
    return f"{REFRESH_KEY_PREFIX}{jti}"


def user_refresh_set_key(user_id: int) -> str:
    return f"{USER_REFRESH_SET_PREFIX}{user_id}{USER_REFRESH_SET_SUFFIX}"


def password_reset_key(token: str) -> str:
    return f"{PASSWORD_RESET_KEY_PREFIX}{token}"


def refresh_token_ttl_seconds() -> int:
    return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


async def store_refresh_token(user_id: int, jti: str) -> None:
    ttl = refresh_token_ttl_seconds()
    await redis_client.set(refresh_token_key(jti), str(user_id), ex=ttl)
    await redis_client.sadd(user_refresh_set_key(user_id), jti)


async def is_refresh_token_active(jti: str, user_id: int) -> bool:
    stored_user_id = await redis_client.get(refresh_token_key(jti))
    return stored_user_id is not None and stored_user_id == str(user_id)


async def revoke_refresh_token(user_id: int, jti: str) -> None:
    await redis_client.delete(refresh_token_key(jti))
    await redis_client.srem(user_refresh_set_key(user_id), jti)


async def revoke_all_refresh_tokens(user_id: int) -> None:
    jtis = await redis_client.smembers(user_refresh_set_key(user_id))
    if jtis:
        await redis_client.delete(*[refresh_token_key(jti) for jti in jtis])
    await redis_client.delete(user_refresh_set_key(user_id))


async def store_password_reset_token(token: str, user_id: int) -> None:
    ttl = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES * 60
    await redis_client.set(password_reset_key(token), str(user_id), ex=ttl)


async def pop_password_reset_user_id(token: str) -> int | None:
    key = password_reset_key(token)
    user_id_raw = await redis_client.get(key)
    if user_id_raw is None:
        return None
    await redis_client.delete(key)
    try:
        return int(user_id_raw)
    except ValueError:
        return None
