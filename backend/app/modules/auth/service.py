"""الدخول وتدوير رموز التحديث وكشف إعادة الاستخدام (13-security)."""
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SYSTEM_USER_ID, AuditContext, set_audit_context
from app.core.errors import DomainError, Unauthorized
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_refresh_token,
    validate_password_policy,
    verify_password,
)
from app.modules.users.models import RefreshToken, User
from app.modules.users.service import revoke_all_sessions

IDLE_TIMEOUT = timedelta(minutes=30)
_DUMMY_HASH = hash_password("dummy-password-for-timing-1")
INVALID_CREDENTIALS = "اسم المستخدم أو كلمة المرور غير صحيحة."


class AccountLocked(DomainError):
    status_code = 423
    code = "ACCOUNT_LOCKED"


@dataclass
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    must_change_password: bool


def _audit_event(session: Session, action: str, user_id: uuid.UUID | None, data: dict) -> None:
    session.execute(text("SELECT audit_append(:a, 'users', :r, NULL, CAST(:d AS jsonb))"),
                    {"a": action, "r": str(user_id) if user_id else None, "d": json.dumps(data)})


def _ctx(session: Session, user_id: uuid.UUID, ip: str | None, ua: str | None) -> None:
    set_audit_context(session, AuditContext(user_id=user_id, ip=ip, user_agent=ua))


def _as_user(session: Session, user_id: uuid.UUID) -> None:
    session.execute(text("SELECT set_config('gbcfms.user_id', :u, true)"), {"u": str(user_id)})


def _issue(session: Session, user: User, family_id: uuid.UUID, expires_at: datetime, ip: str | None,
           ua: str | None) -> tuple[RefreshToken, IssuedTokens]:
    raw, digest = new_refresh_token()
    token = RefreshToken(user_id=user.id, token_hash=digest, family_id=family_id, expires_at=expires_at,
                         ip=ip, user_agent=(ua or "")[:500])
    session.add(token)
    session.flush()
    access, ttl = create_access_token(user.id, family_id, user.auth_version)
    return token, IssuedTokens(access, ttl, raw, expires_at, user.must_change_password)


def login(session: Session, username: str, password: str, ip: str | None, ua: str | None) -> IssuedTokens:
    s = get_settings()
    now = datetime.now(UTC)
    _ctx(session, SYSTEM_USER_ID, ip, ua)
    user = session.scalar(select(User).where(User.username == username.strip()).with_for_update())
    if user is None or user.is_system or user.deleted_at is not None:
        verify_password(_DUMMY_HASH, password)  # زمن ثابت تقريبًا لمنع تعداد الحسابات
        _audit_event(session, "LOGIN_FAILED", None, {"username": username[:60], "reason": "unknown_user"})
        session.commit()
        raise Unauthorized(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")
    _as_user(session, user.id)
    if user.locked_until and user.locked_until > now:
        _audit_event(session, "LOGIN_BLOCKED", user.id, {"locked_until": user.locked_until.isoformat()})
        session.commit()
        raise AccountLocked("الحساب مقفل مؤقتًا بسبب محاولات فاشلة متكررة.",
                            details={"locked_until": user.locked_until.isoformat()})
    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= s.max_failed_logins:
            factor = 2 ** min((user.failed_logins - s.max_failed_logins) // s.max_failed_logins, 6)
            user.locked_until = now + timedelta(minutes=s.lockout_minutes * factor)
        _audit_event(session, "LOGIN_FAILED", user.id, {"failed_logins": user.failed_logins})
        session.commit()
        raise Unauthorized(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")
    if not user.is_active:
        _audit_event(session, "LOGIN_FAILED", user.id, {"reason": "inactive"})
        session.commit()
        raise Unauthorized(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    _, tokens = _issue(session, user, uuid.uuid4(), now + timedelta(hours=s.refresh_token_hours), ip, ua)
    _audit_event(session, "LOGIN_SUCCESS", user.id, {})
    session.commit()
    return tokens


def refresh(session: Session, raw_token: str, ip: str | None, ua: str | None) -> IssuedTokens:
    now = datetime.now(UTC)
    _ctx(session, SYSTEM_USER_ID, ip, ua)
    token = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
                           .with_for_update())
    if token is None:
        raise Unauthorized("رمز التحديث غير صالح.", code="INVALID_REFRESH")
    _as_user(session, token.user_id)
    if token.revoked_at is not None:
        if token.replaced_by is not None:
            # إعادة استخدام رمز سبق تدويره = احتمال سرقة: إلغاء العائلة كلها
            session.execute(update(RefreshToken).where(RefreshToken.family_id == token.family_id,
                                                       RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
            _audit_event(session, "REFRESH_REUSE_DETECTED", token.user_id, {"family_id": str(token.family_id)})
            session.commit()
        raise Unauthorized("انتهت الجلسة، يرجى تسجيل الدخول.", code="INVALID_REFRESH")
    last = token.last_used_at or token.issued_at
    if token.expires_at <= now or now - last > IDLE_TIMEOUT:
        token.revoked_at = now
        session.commit()
        raise Unauthorized("انتهت الجلسة، يرجى تسجيل الدخول.", code="SESSION_EXPIRED")
    user = session.get(User, token.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        token.revoked_at = now
        session.commit()
        raise Unauthorized("الحساب غير نشط.", code="INACTIVE_ACCOUNT")
    new_token, tokens = _issue(session, user, token.family_id, token.expires_at, ip, ua)
    new_token.last_used_at = now
    token.revoked_at = now
    token.replaced_by = new_token.id
    session.commit()
    return tokens


def logout(session: Session, user_id: uuid.UUID, family_id: uuid.UUID, ip: str | None, ua: str | None) -> None:
    _ctx(session, user_id, ip, ua)
    session.execute(update(RefreshToken).where(RefreshToken.family_id == family_id,
                                               RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=datetime.now(UTC)))
    session.commit()


def change_password(session: Session, user_id: uuid.UUID, current: str, new: str, ip: str | None,
                    ua: str | None) -> IssuedTokens:
    s = get_settings()
    _ctx(session, user_id, ip, ua)
    user = session.get(User, user_id, with_for_update=True)
    if not verify_password(user.password_hash, current):
        raise Unauthorized("كلمة المرور الحالية غير صحيحة.", code="INVALID_CREDENTIALS")
    if verify_password(user.password_hash, new):
        raise DomainError("كلمة المرور الجديدة يجب أن تختلف عن الحالية.", code="PASSWORD_REUSE", status_code=422)
    validate_password_policy(new, user.username)
    now = datetime.now(UTC)
    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.password_changed_at = now
    user.auth_version += 1
    revoke_all_sessions(session, user.id)
    _, tokens = _issue(session, user, uuid.uuid4(), now + timedelta(hours=s.refresh_token_hours), ip, ua)
    session.commit()
    return tokens


def active_sessions(session: Session, user_id: uuid.UUID) -> list[RefreshToken]:
    return list(session.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id,
                                                           RefreshToken.revoked_at.is_(None))
                                .order_by(RefreshToken.issued_at.desc())))
