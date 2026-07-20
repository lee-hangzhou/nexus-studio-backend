from typing import Callable, List, Optional, Set

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.security import decode_token


class JWTAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        whitelist: Optional[List[str]] = None,
        whitelist_prefixes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(app)
        self.whitelist: Set[str] = set(whitelist or [])
        self.whitelist_prefixes: List[str] = whitelist_prefixes or []

    def _is_whitelisted(self, path: str) -> bool:
        if path in self.whitelist:
            return True

        for prefix in self.whitelist_prefixes:
            if path.startswith(prefix):
                return True

        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        if self._is_whitelisted(path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")

        if not auth_header:
            query_token = request.query_params.get("token")
            if query_token and request.method in {"GET", "HEAD", "OPTIONS"}:
                auth_header = f"Bearer {query_token}"

        if not auth_header:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": 401,
                    "message": "Missing authorization header",
                    "data": None,
                },
            )

        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": 401,
                    "message": "Invalid authorization header format",
                    "data": None,
                },
            )

        token = auth_header[7:]  # Remove "Bearer " prefix
        payload = decode_token(token)

        if not payload:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": 401,
                    "message": "Invalid or expired token",
                    "data": None,
                },
            )

        if payload.get("type") != "access":
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": 401,
                    "message": "Invalid token type",
                    "data": None,
                },
            )

        sub = payload.get("sub")
        try:
            user_id = int(sub) if sub is not None else None
        except (TypeError, ValueError):
            user_id = None

        if user_id is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": 401,
                    "message": "Invalid token subject",
                    "data": None,
                },
            )

        request.state.user_id = user_id
        request.state.user_roles = payload.get("roles", [])
        request.state.token_payload = payload

        return await call_next(request)
