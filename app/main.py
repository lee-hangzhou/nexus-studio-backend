import uvicorn
from fastapi import FastAPI

import app.composition  # noqa: F401
from app.server.api.v1.router import api_router
from app.server.infra.config import settings
from app.server.infra.lifespan import lifespan
from app.server.exceptions.handlers import register_exception_handlers
from app.server.middleware import (
    JWTAuthMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    TracingMiddleware,
    setup_cors,
)


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    setup_cors(application)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(TracingMiddleware)
    application.add_middleware(RequestLoggingMiddleware)

    auth_prefix = f"{settings.API_V1_PREFIX}/auth"
    application.add_middleware(
        JWTAuthMiddleware,
        whitelist={
            "/docs",
            "/redoc",
            f"{settings.API_V1_PREFIX}/openapi.json",
            f"{auth_prefix}/send-register-code",
            f"{auth_prefix}/register",
            f"{auth_prefix}/login",
            f"{auth_prefix}/refresh",
            f"{auth_prefix}/forgot-password",
            f"{auth_prefix}/reset-password",
        },
        whitelist_prefixes=[
            f"{settings.API_V1_PREFIX}/health",
            f"{settings.API_V1_PREFIX}/gateway/callbacks",
            f"{settings.API_V1_PREFIX}/generate/callback",
        ],
    )

    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.API_V1_PREFIX)

    return application


app = create_app()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)
