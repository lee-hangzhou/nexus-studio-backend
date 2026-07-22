from typing import Dict, List, Optional, Set, Tuple

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.security import decode_token


class AuthRequired:
    def __init__(
        self,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        auto_error: bool = True,
    ) -> None:
        self.roles: Optional[set[str]] = set(roles) if roles else None
        self.permissions: Optional[set[str]] = set(permissions) if permissions else None
        self.auto_error = auto_error
        self.security = HTTPBearer(auto_error=auto_error)

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
    ) -> Optional[int]:
        if credentials is None:
            credentials = await self.security(request)

        if not credentials:
            if self.auto_error:
                raise AppError(ErrorCode.INVALID_TOKEN)
            return None

        payload = decode_token(credentials.credentials)
        if not payload:
            if self.auto_error:
                raise AppError(ErrorCode.INVALID_TOKEN)
            return None

        if payload.get("type") != "access":
            if self.auto_error:
                raise AppError(ErrorCode.INVALID_TOKEN)
            return None

        user_id_value = payload.get("sub")
        if user_id_value is None:
            if self.auto_error:
                raise AppError(ErrorCode.INVALID_TOKEN)
            return None

        try:
            user_id = int(user_id_value)
        except (ValueError, TypeError) as e:
            if self.auto_error:
                raise AppError(ErrorCode.INVALID_TOKEN) from e
            return None

        if self.roles:
            user_roles = set(payload.get("roles", []))
            if not user_roles.intersection(self.roles):
                raise AppError(ErrorCode.PERMISSION_DENIED)

        if self.permissions:
            user_permissions = set(payload.get("permissions", []))
            if not user_permissions.issuperset(self.permissions):
                raise AppError(ErrorCode.PERMISSION_DENIED)

        request.state.user_id = user_id
        request.state.user_roles = payload.get("roles", [])

        return user_id


class WhitelistChecker:
    def __init__(self, whitelist: List[Tuple[str, List[str]]]) -> None:
        self._whitelist: Dict[str, Set[str]] = {}
        for path, methods in whitelist:
            self._whitelist[path] = {m.upper() for m in methods}

    def is_allowed(self, path: str, method: str) -> bool:
        if path in self._whitelist:
            methods = self._whitelist[path]
            return method.upper() in methods or "*" in methods

        for whitelist_path, methods in self._whitelist.items():
            if whitelist_path.endswith("*"):
                prefix = whitelist_path[:-1]
                if path.startswith(prefix):
                    return method.upper() in methods or "*" in methods

        return False


auth_required = AuthRequired()
admin_required = AuthRequired(roles=["admin"])
