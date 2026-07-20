import json
import time
from typing import Callable, Dict, Optional, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logger import logger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    MAX_BODY_LOG_SIZE = 1_000_000
    SENSITIVE_BODY_FIELDS = {
        "access_token",
        "code",
        "new_password",
        "otp",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
    # SSE：不可读 body / 不可包一层消费 receive，否则 StreamingResponse 断连时报 http.request
    STREAMING_PATH_PREFIXES = (
        "/api/v1/chat/message/stream",
        "/api/v1/chat/turn/resume",
    )

    def __init__(
        self,
        app,
        log_request_body: bool = True,
        log_query_params: bool = True,
        exclude_paths: Optional[list[str]] = None,
    ) -> None:
        super().__init__(app)
        self.log_request_body = log_request_body
        self.log_query_params = log_query_params
        self.exclude_paths = set(exclude_paths or ["/health", "/metrics"])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        request_body: Optional[str] = None
        query_params: Optional[dict[str, str]] = None

        if self.log_request_body and not self._is_streaming_path(request.url.path):
            request_body = await self._get_request_body(request)
            request_body = self._redact_request_body(request.url.path, request_body)
        if self.log_query_params:
            params = dict(request.query_params)
            query_params = params if params else None

        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                request_body=request_body,
                query_params=query_params,
                client_ip=self._get_client_ip(request),
            )
            raise

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        log_kwargs: Dict[str, Union[str, int, float, dict, None]] = {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": self._get_client_ip(request),
        }

        if request_body:
            log_kwargs["request_body"] = request_body
        if query_params:
            log_kwargs["query_params"] = query_params

        if response.status_code >= 500:
            logger.error("request_completed", **log_kwargs)
        elif response.status_code >= 400:
            logger.warning("request_completed", **log_kwargs)
        else:
            logger.info("request_completed", **log_kwargs)

        return response

    @classmethod
    def _is_streaming_path(cls, path: str) -> bool:
        if any(path.startswith(prefix) for prefix in cls.STREAMING_PATH_PREFIXES):
            return True
        # 画布 Agent turn / resume / 节点生成 SSE（与 chat/message/stream 同约束）
        if path.startswith("/api/v1/canvas/"):
            if path.endswith("/turn") or path.endswith("/turn/resume"):
                return True
            if "/nodes/" in path and path.endswith("/generate"):
                return True
        return False

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP from request, considering proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    @classmethod
    def _redact_request_body(cls, path: str, body: Optional[str]) -> Optional[str]:
        if body is None:
            return body
        if path.startswith("/api/v1/auth/") or path == "/api/v1/chat/turn/resume":
            try:
                payload = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                return "[redacted body]" if path.startswith("/api/v1/auth/") else body

            def redact(value, parent_key: str | None = None):
                if isinstance(value, dict):
                    if parent_key == "fields":
                        return {key: "[REDACTED]" for key in value}
                    return {
                        key: "[REDACTED]" if key.lower() in cls.SENSITIVE_BODY_FIELDS else redact(item, key)
                        for key, item in value.items()
                    }
                if isinstance(value, list):
                    return [redact(item, parent_key) for item in value]
                return value

            return json.dumps(redact(payload), ensure_ascii=False)
        return body

    async def _get_request_body(self, request: Request) -> Optional[str]:
        """Read and return request body for logging."""
        if request.method not in {"POST", "PUT", "PATCH"}:
            return None

        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            return "[multipart/form-data]"

        try:
            body_bytes = await request.body()
            body_sent = False

            async def receive() -> Dict[str, Union[str, bytes]]:
                nonlocal body_sent
                if body_sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                body_sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            request._receive = receive

            if len(body_bytes) > self.MAX_BODY_LOG_SIZE:
                return "[body too large]"

            return body_bytes.decode("utf-8") if body_bytes else None
        except (UnicodeDecodeError, RuntimeError):
            return "[unable to read body]"
