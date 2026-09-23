"""نقطة دخول الخادم: نظام مراقبة الاعتمادات والمصروفات الحكومية (GBCFMS)."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router

API_PREFIX = "/api/v1"


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if get_settings().env == "prod":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="نظام مراقبة الاعتمادات والمصروفات الحكومية",
        description="Government Budget Control & Financial Monitoring System (GBCFMS)",
        version="0.2.0",
        docs_url=None if settings.env == "prod" else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.env == "prod" else "/api/openapi.json",
    )
    app.add_middleware(SecurityHeaders)
    install_error_handlers(app)
    for r in (auth_router, users_router):
        app.include_router(r, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", tags=["النظام"])
    def health():
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
