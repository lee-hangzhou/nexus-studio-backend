from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.exceptions.response import json_error_response
from app.server.infra.logger import log_exception, logger


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "app_error",
        error_code=exc.code,
        error_message=exc.message,
        path=request.url.path,
        method=request.method,
    )
    headers: dict[str, str] | None = None
    retry_after = (exc.details or {}).get("retry_after")
    if retry_after is not None and exc.status_code == 429:
        headers = {"Retry-After": str(retry_after)}
    return json_error_response(
        code=exc.code,
        msg=exc.message,
        data=exc.details,
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    logger.warning(
        "validation_error",
        errors=errors,
        path=request.url.path,
        method=request.method,
    )
    summary = [
        {
            "loc": list(item.get("loc", ())),
            "msg": item.get("msg"),
            "type": item.get("type"),
        }
        for item in errors
    ]
    return json_error_response(
        code=int(ErrorCode.INVALID_PARAMS),
        msg="Validation error",
        data=summary,
    )


async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return json_error_response(
        code=exc.status_code,
        msg=detail or "HTTP error",
        status_code=exc.status_code,
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log_exception(
        "unhandled_error",
        exc=exc,
        path=request.url.path,
        method=request.method,
    )
    return json_error_response(
        code=int(ErrorCode.INTERNAL_ERROR),
        msg="Internal server error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
