import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, get_principal, get_request_meta
from app.core.errors import NotFound, Unauthorized
from app.core.ratelimit import login_limiter
from app.modules.auth import service
from app.modules.users.service import user_role_codes

router = APIRouter(prefix="/auth", tags=["المصادقة"])
REFRESH_COOKIE = "gbcfms_refresh"
COOKIE_PATH = "/api/v1/auth"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    must_change_password: bool


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class MeOut(BaseModel):
    id: str
    username: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    scopes: dict[str, list[str]]
    must_change_password: bool


class SessionOut(BaseModel):
    id: str
    issued_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


def _set_cookie(response: Response, tokens: service.IssuedTokens) -> TokenOut:
    s = get_settings()
    secure = s.cookie_secure if s.cookie_secure is not None else s.env == "prod"
    response.set_cookie(REFRESH_COOKIE, tokens.refresh_token, httponly=True,
                        secure=secure, samesite="strict", path=COOKIE_PATH,
                        expires=tokens.refresh_expires_at.astimezone(UTC))
    return TokenOut(access_token=tokens.access_token, expires_in=tokens.expires_in,
                    must_change_password=tokens.must_change_password)


def _check_origin(request: Request) -> None:
    """حماية CSRF لنقاط الكوكي: الطلب يجب أن يأتي من نفس الأصل إذا أرسل المتصفح Origin."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        allowed = get_settings().env != "prod" and origin.startswith(("http://localhost", "http://127.0.0.1"))
        if not allowed:
            raise Unauthorized("مصدر الطلب غير مسموح.", code="BAD_ORIGIN")


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, response: Response, meta: RequestMeta = Depends(get_request_meta),
          session: Session = Depends(get_session)):
    login_limiter.hit(f"{meta.ip}:{body.username.lower()}")
    tokens = service.login(session, body.username, body.password, meta.ip, meta.user_agent)
    return _set_cookie(response, tokens)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, meta: RequestMeta = Depends(get_request_meta),
            session: Session = Depends(get_session),
            refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE)):
    _check_origin(request)
    if not refresh_cookie:
        raise Unauthorized("لا توجد جلسة.", code="NO_REFRESH")
    tokens = service.refresh(session, refresh_cookie, meta.ip, meta.user_agent)
    return _set_cookie(response, tokens)


@router.post("/logout", status_code=204)
def logout(response: Response, principal: Principal = Depends(get_principal),
           meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    service.logout(session, principal.user_id, principal.session_id, meta.ip, meta.user_agent)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.post("/change-password", response_model=TokenOut)
def change_password(body: ChangePasswordIn, response: Response, principal: Principal = Depends(get_principal),
                    meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    tokens = service.change_password(session, principal.user_id, body.current_password, body.new_password,
                                     meta.ip, meta.user_agent)
    return _set_cookie(response, tokens)


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal), session: Session = Depends(get_session)):
    return MeOut(id=str(principal.user_id), username=principal.username, full_name=principal.full_name,
                 roles=user_role_codes(session, principal.user_id), permissions=sorted(principal.permissions),
                 scopes={k: sorted(str(i) for i in v) for k, v in principal.scopes.items()},
                 must_change_password=principal.must_change_password)


@router.get("/sessions", response_model=list[SessionOut])
def sessions(principal: Principal = Depends(get_principal), session: Session = Depends(get_session)):
    return [SessionOut(id=str(t.family_id), issued_at=t.issued_at, last_used_at=t.last_used_at,
                       expires_at=t.expires_at, ip=t.ip, user_agent=t.user_agent,
                       current=t.family_id == principal.session_id)
            for t in service.active_sessions(session, principal.user_id)]


@router.delete("/sessions/{family_id}", status_code=204)
def revoke_session(family_id: str, principal: Principal = Depends(get_principal),
                   meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    families = {str(t.family_id) for t in service.active_sessions(session, principal.user_id)}
    if family_id not in families:
        raise NotFound("الجلسة غير موجودة.")
    service.logout(session, principal.user_id, uuid.UUID(family_id), meta.ip, meta.user_agent)
