"""أخطاء النطاق وتحويلها إلى استجابات RFC 7807 برسائل عربية."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None,
                 status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class Unauthorized(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"


def _problem(status: int, code: str, title: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": f"urn:gbcfms:error:{code.lower()}", "code": code, "title": title,
                 "status": status, "details": details or {}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError):
        return _problem(exc.status_code, exc.code, exc.message, exc.details)

    from sqlalchemy.exc import DBAPIError, IntegrityError

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        # شبكة أمان: قيود قاعدة البيانات هي خط الدفاع الأخير (التكرار، والمفاتيح، والفحوص)
        return _problem(409, "CONSTRAINT_VIOLATION", "العملية تخالف قيدًا في قاعدة البيانات.",
                        {"constraint": getattr(getattr(exc.orig, "diag", None), "constraint_name", None)})

    @app.exception_handler(DBAPIError)
    async def _dbapi(_: Request, exc: DBAPIError):
        # رسائل triggers الحماية (P0001/42501) عربية وموجهة للمستخدم
        code = getattr(exc.orig, "sqlstate", None)
        if code in ("P0001", "42501"):
            msg = str(getattr(getattr(exc.orig, "diag", None), "message_primary", "") or "العملية مرفوضة.")
            return _problem(409, "RULE_VIOLATION", msg)
        raise exc

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
                  for e in exc.errors()]
        return _problem(422, "VALIDATION_FAILED", "البيانات المدخلة غير صالحة.", {"errors": errors})
