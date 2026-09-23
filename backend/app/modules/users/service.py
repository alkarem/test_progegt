"""خدمات المستخدمين والأدوار والنطاقات."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.permissions import PERMISSIONS, ROLES
from app.core.security import hash_password, validate_password_policy
from app.modules.users.models import Permission, RefreshToken, Role, RolePermission, User, UserRole, UserScope

SCOPE_TABLES = {"FISCAL_YEAR": "fiscal_years", "ENTITY": "entities", "CHAPTER": "budget_chapters",
                "ITEM": "budget_items"}


def sync_permissions_and_roles(session: Session) -> None:
    """يزامن الكتالوج في الكود مع قاعدة البيانات. الأدوار النظامية تُضبط صلاحياتها حرفيًا."""
    existing = {p.code: p for p in session.scalars(select(Permission))}
    for code, desc in PERMISSIONS.items():
        module, action = code.split(".", 1)
        if code in existing:
            existing[code].description = desc
        else:
            session.add(Permission(code=code, module=module, action=action, description=desc))
    session.flush()
    for code, (name, perms) in ROLES.items():
        role = session.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name, is_system=True)
            session.add(role)
            session.flush()
        role.name, role.is_system = name, True
        current = set(session.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == role.id)))
        for p in perms - current:
            session.add(RolePermission(role_id=role.id, permission_code=p))
        stale = current - perms
        if stale:
            session.execute(delete(RolePermission).where(RolePermission.role_id == role.id,
                                                         RolePermission.permission_code.in_(stale)))
    obsolete = set(existing) - PERMISSIONS.keys()
    if obsolete:
        session.execute(delete(Permission).where(Permission.code.in_(obsolete)))
    session.flush()


def get_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None or user.is_system or user.deleted_at is not None:
        raise NotFound("المستخدم غير موجود.")
    return user


def create_user(session: Session, *, username: str, full_name: str, email: str | None, password: str,
                role_codes: list[str]) -> User:
    username = username.strip()
    if session.scalar(select(User.id).where(User.username == username)):
        raise Conflict("اسم المستخدم مستخدم مسبقًا.", code="DUPLICATE_USERNAME")
    if email and session.scalar(select(User.id).where(User.email == email)):
        raise Conflict("البريد الإلكتروني مستخدم مسبقًا.", code="DUPLICATE_EMAIL")
    validate_password_policy(password, username)
    user = User(username=username, full_name=full_name.strip(), email=email, password_hash=hash_password(password),
                must_change_password=True)
    session.add(user)
    session.flush()
    set_roles(session, user, role_codes)
    return user


def set_roles(session: Session, user: User, role_codes: list[str]) -> None:
    roles = session.scalars(select(Role).where(Role.code.in_(role_codes))).all()
    missing = set(role_codes) - {r.code for r in roles}
    if missing:
        raise ValidationFailed("أدوار غير معروفة.", code="UNKNOWN_ROLE", details={"roles": sorted(missing)})
    session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for r in roles:
        session.add(UserRole(user_id=user.id, role_id=r.id))
    _bump_auth(session, user)


def set_scopes(session: Session, user: User, scopes: dict[str, list[uuid.UUID]]) -> None:
    for scope_type, ids in scopes.items():
        table = SCOPE_TABLES.get(scope_type)
        if table is None:
            raise ValidationFailed("نوع نطاق غير معروف.", code="UNKNOWN_SCOPE", details={"scope": scope_type})
        if ids:
            found = session.execute(text(f"SELECT count(*) FROM {table} WHERE id = ANY(:ids)"),  # noqa: S608
                                    {"ids": list(ids)}).scalar()
            if found != len(set(ids)):
                raise ValidationFailed("معرفات نطاق غير موجودة.", code="UNKNOWN_SCOPE_ID",
                                       details={"scope": scope_type})
    session.execute(delete(UserScope).where(UserScope.user_id == user.id))
    for scope_type, ids in scopes.items():
        for sid in set(ids):
            session.add(UserScope(user_id=user.id, scope_type=scope_type, scope_id=sid))
    _bump_auth(session, user)


def user_role_codes(session: Session, user_id: uuid.UUID) -> list[str]:
    return list(session.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id)
                                .where(UserRole.user_id == user_id).order_by(Role.code)))


def user_scopes(session: Session, user_id: uuid.UUID) -> dict[str, list[uuid.UUID]]:
    out: dict[str, list[uuid.UUID]] = {}
    for s in session.scalars(select(UserScope).where(UserScope.user_id == user_id)):
        out.setdefault(s.scope_type, []).append(s.scope_id)
    return out


def revoke_all_sessions(session: Session, user_id: uuid.UUID) -> None:
    session.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=datetime.now(UTC)))


def _bump_auth(session: Session, user: User) -> None:
    """تغيير الصلاحيات يبطل رموز الدخول الحالية فورًا (13-security: الجلسات)."""
    user.auth_version += 1
