"""نقطة دخول الخادم: نظام مراقبة الاعتمادات والمصروفات الحكومية (GBCFMS)."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.modules import handlers  # noqa: F401  (تسجيل معالجات المستندات)
from app.modules.adjustments.router import router as adjustments_router
from app.modules.ai.router import router as ai_router
from app.modules.alerts.router import router as alerts_router
from app.modules.attachments.router import router as attachments_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.authorizations.router import router as authorizations_router
from app.modules.backup.router import router as backup_router
from app.modules.budget.router import router as budget_router
from app.modules.catalog.router import router as catalog_router
from app.modules.commitments.router import router as commitments_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.expenditures.router import router as expenditures_router
from app.modules.fiscal.router import router as fiscal_router
from app.modules.imports.router import router as imports_router
from app.modules.ledger.router import router as ledger_router
from app.modules.reports.router import router as reports_router
from app.modules.search.router import router as search_router
from app.modules.transfers.router import router as transfers_router
from app.modules.users.router import router as users_router
from app.modules.workflow.router import router as workflow_router

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
    for r in (auth_router, users_router, catalog_router, fiscal_router, ledger_router, budget_router,
              workflow_router, authorizations_router, attachments_router,
              transfers_router, commitments_router, expenditures_router, adjustments_router,
              alerts_router, dashboard_router, search_router, reports_router,
              imports_router, audit_router, backup_router, ai_router):
        app.include_router(r, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", tags=["النظام"])
    def health():
        return JSONResponse({"status": "ok"})

    if settings.web_dir and (Path(settings.web_dir) / "index.html").is_file():
        _serve_web(app, Path(settings.web_dir).resolve())
    return app


CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self';"
       " connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def _serve_web(app: FastAPI, web: Path) -> None:
    """تقديم الواجهة المبنية من الخادم نفسه عند التشغيل بدون nginx (Windows مباشرة)."""
    index = web / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"type": "about:blank", "title": "غير موجود", "status": 404}, status_code=404)
        target = (web / path).resolve()
        if path and target.is_file() and web in target.parents:
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})


app = create_app()
