import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt

from app.server.infra.config import settings

password_hasher = PasswordHasher()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        result = password_hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        return False

    if not isinstance(result, bool):
        raise TypeError(f"Expected bool from verify, got {type(result)}")
    return result


def new_jti() -> str:
    return str(uuid4())


def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def get_password_hash(password: str) -> str:
    result = password_hasher.hash(password)
    if not isinstance(result, str):
        raise TypeError(f"Expected str from hash, got {type(result)}")
    return result


def create_access_token(
    subject: Union[str, int],
    expires_delta: Optional[timedelta] = None,
    extra_data: Optional[Dict[str, Any]] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: Dict[str, Any] = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
        "jti": new_jti(),
    }
    if extra_data:
        to_encode.update(extra_data)

    result = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    if not isinstance(result, str):
        raise TypeError(f"Expected str from jwt.encode, got {type(result)}")
    return result


def create_refresh_token(
    subject: Union[str, int],
    expires_delta: Optional[timedelta] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode: Dict[str, Any] = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "jti": new_jti(),
    }
    result = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    if not isinstance(result, str):
        raise TypeError(f"Expected str from jwt.encode, got {type(result)}")
    return result


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if not isinstance(payload, dict):
            return None
        return payload
    except JWTError:
        return None
