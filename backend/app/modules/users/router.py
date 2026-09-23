import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.core.errors import ValidationFailed
from app.core.security import hash_password, validate_password_policy
from app.modules.users import service
from app.modules.users.models import Permission, Role, RolePermission, User
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(tags=["المستخدمون والصلاحيات"])


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    email: str | None
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    roles: list[str]
    scopes: dict[str, list[uuid.UUID]]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None
    password: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: EmailStr | None = None
    is_active: bool | None = None


class RolesIn(BaseModel):
    roles: list[str]


class ScopesIn(BaseModel):
    scopes: dict[str, list[uuid.UUID]]


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=128)


class RoleOut(BaseModel):
    code: str
    name: str
    is_system: bool
    permissions: list[str]


class PermissionOut(BaseModel):
    code: str
    module: str
    action: str
    description: str


def _out(session: Session, u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, full_name=u.full_name, email=u.email, is_active=u.is_active,
                   must_change_password=u.must_change_password, locked_until=u.locked_until,
                   last_login_at=u.last_login_at, roles=service.user_role_codes(session, u.id),
                   scopes=service.user_scopes(session, u.id))


@router.get("/users", response_model=Page[UserOut])
def list_users(params: PageParams = Depends(), _: Principal = Depends(require("users.view")),
               session: Session = Depends(get_session)):
    stmt = select(User).where(User.is_system.is_(False), User.deleted_at.is_(None)).order_by(User.username)
    return paginate(session, stmt, params, lambda r: _out(session, r[0]))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, p: Principal = Depends(require("users.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    user = service.create_user(session, username=body.username, full_name=body.full_name, email=body.email,
                               password=body.password, role_codes=body.roles)
    session.commit()
    return _out(session, user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, _: Principal = Depends(require("users.view")),
             session: Session = Depends(get_session)):
    return _out(session, service.get_user(session, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, p: Principal = Depends(require("users.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    user = service.get_user(session, user_id)
    if body.is_active is False and user.id == p.user_id:
        raise ValidationFailed("لا يمكنك تعطيل حسابك.", code="SELF_DEACTIVATION")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    if body.is_active is False:
        service.revoke_all_sessions(session, user.id)
        user.auth_version += 1
    session.commit()
    return _out(session, user)


@router.put("/users/{user_id}/roles", response_model=UserOut)
def set_roles(user_id: uuid.UUID, body: RolesIn, p: Principal = Depends(require("users.manage")),
              meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    user = service.get_user(session, user_id)
    service.set_roles(session, user, body.roles)
    session.commit()
    return _out(session, user)


@router.put("/users/{user_id}/scopes", response_model=UserOut)
def set_scopes(user_id: uuid.UUID, body: ScopesIn, p: Principal = Depends(require("users.manage")),
               meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    user = service.get_user(session, user_id)
    service.set_scopes(session, user, body.scopes)
    session.commit()
    return _out(session, user)


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(user_id: uuid.UUID, body: ResetPasswordIn, p: Principal = Depends(require("users.manage")),
                   meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason="إعادة تعيين كلمة المرور")
    user = service.get_user(session, user_id)
    validate_password_policy(body.new_password, user.username)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    user.auth_version += 1
    service.revoke_all_sessions(session, user.id)
    session.commit()
    return _out(session, user)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: Principal = Depends(require("users.view")), session: Session = Depends(get_session)):
    out = []
    for r in session.scalars(select(Role).order_by(Role.code)):
        perms = session.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == r.id)
                                .order_by(RolePermission.permission_code)).all()
        out.append(RoleOut(code=r.code, name=r.name, is_system=r.is_system, permissions=list(perms)))
    return out


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(_: Principal = Depends(require("users.view")), session: Session = Depends(get_session)):
    return [PermissionOut(code=p.code, module=p.module, action=p.action, description=p.description)
            for p in session.scalars(select(Permission).order_by(Permission.code))]
