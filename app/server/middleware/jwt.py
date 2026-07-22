from typing import Callable, List, Optional, Set

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.server.exceptions.codes import ErrorCode
from app.server.exceptions.response import json_error_response
from app.server.infra.security import decode_token


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
            return json_error_response(
                code=int(ErrorCode.INVALID_TOKEN),
                msg="Missing authorization header",
            )

        if not auth_header.startswith("Bearer "):
            return json_error_response(
                code=int(ErrorCode.INVALID_TOKEN),
                msg="Invalid authorization header format",
            )

        token = auth_header[7:]
        payload = decode_token(token)

        if not payload:
            return json_error_response(
                code=int(ErrorCode.INVALID_TOKEN),
                msg="Invalid or expired token",
            )

        if payload.get("type") != "access":
            return json_error_response(
                code=int(ErrorCode.INVALID_TOKEN),
                msg="Invalid token type",
            )

        sub = payload.get("sub")
        try:
            user_id = int(sub) if sub is not None else None
        except (TypeError, ValueError):
            user_id = None

        if user_id is None:
            return json_error_response(
                code=int(ErrorCode.INVALID_TOKEN),
                msg="Invalid token subject",
            )

        request.state.user_id = user_id
        request.state.user_roles = payload.get("roles", [])
        request.state.token_payload = payload

        return await call_next(request)
