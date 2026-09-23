"""الاعتماديات المشتركة: الجلسة، والمستخدم الحالي، والصلاحيات، وسياق التدقيق للطلب."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import AuditContext, get_session, set_audit_context
from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_token
from app.modules.users.models import RefreshToken, RolePermission, User, UserRole, UserScope

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: uuid.UUID
    username: str
    full_name: str
    session_id: uuid.UUID
    permissions: frozenset[str]
    # نوع النطاق ← المعرفات المسموح بها. غياب النوع = كل القيم (06-permissions §1)
    scopes: dict[str, frozenset[uuid.UUID]] = field(default_factory=dict)
    must_change_password: bool = False

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if perm not in self.permissions:
            raise Forbidden("ليست لديك صلاحية لتنفيذ هذا الإجراء.", code="PERMISSION_DENIED",
                            details={"permission": perm})

    def allows(self, scope_type: str, value: uuid.UUID | None) -> bool:
        allowed = self.scopes.get(scope_type)
        return allowed is None or (value is not None and value in allowed)

    def require_scope(self, scope_type: str, value: uuid.UUID | None) -> None:
        if not self.allows(scope_type, value):
            raise Forbidden("السجل خارج نطاق صلاحياتك.", code="OUT_OF_SCOPE", details={"scope": scope_type})


def load_principal(session: Session, user: User, session_id: uuid.UUID) -> Principal:
    perms = session.scalars(
        select(RolePermission.permission_code).join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user.id)
    ).all()
    scopes: dict[str, set[uuid.UUID]] = {}
    for sc in session.scalars(select(UserScope).where(UserScope.user_id == user.id)):
        scopes.setdefault(sc.scope_type, set()).add(sc.scope_id)
    return Principal(user_id=user.id, username=user.username, full_name=user.full_name, session_id=session_id,
                     permissions=frozenset(perms), scopes={k: frozenset(v) for k, v in scopes.items()},
                     must_change_password=user.must_change_password)


def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("يجب تسجيل الدخول.", code="NOT_AUTHENTICATED")
    payload = decode_access_token(creds.credentials)
    user = session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active or user.deleted_at is not None or user.is_system:
        raise Unauthorized("الحساب غير نشط.", code="INACTIVE_ACCOUNT")
    if payload["av"] != user.auth_version:
        raise Unauthorized("انتهت الجلسة بسبب تغيير بيانات الدخول أو الصلاحيات.", code="SESSION_REVOKED")
    sid = uuid.UUID(payload["sid"])
    family = session.scalar(select(RefreshToken.id).where(RefreshToken.family_id == sid,
                                                          RefreshToken.revoked_at.is_(None)).limit(1))
    if family is None:
        raise Unauthorized("انتهت الجلسة.", code="SESSION_REVOKED")
    return load_principal(session, user, sid)


def require(*perms: str) -> Callable[[Principal], Principal]:
    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.must_change_password:
            raise Forbidden("يجب تغيير كلمة المرور أولًا.", code="PASSWORD_CHANGE_REQUIRED")
        for p in perms:
            principal.require(p)
        return principal
    return dep


@dataclass
class RequestMeta:
    ip: str | None
    user_agent: str | None
    request_id: str


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=request.client.host if request.client else None,
                       user_agent=request.headers.get("user-agent"),
                       request_id=request.headers.get("x-request-id") or uuid.uuid4().hex)


def begin_write(session: Session, user_id: uuid.UUID, meta: RequestMeta | None, reason: str | None = None) -> None:
    set_audit_context(session, AuditContext(user_id=user_id, ip=meta.ip if meta else None,
                                            user_agent=meta.user_agent if meta else None,
                                            request_id=meta.request_id if meta else None, reason=reason))
