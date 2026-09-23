import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.backup import service
from app.modules.backup.models import Backup
from app.shared.money import jsonable

router = APIRouter(tags=["النسخ الاحتياطي وصحة النظام"])


class SettingsIn(BaseModel):
    location: str | None = Field(default=None, min_length=1, max_length=500)
    daily_time: str | None = Field(default=None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    enabled: bool | None = None
    retention_count: int | None = Field(default=None, ge=1, le=365)
    include_attachments: bool | None = None


class BackupOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    file_name: str | None
    size_bytes: int | None
    sha256: str | None
    encrypted: bool
    verified_at: datetime | None
    verification: dict | None
    error: str | None


class RestoreIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=100)


@router.get("/backup/settings")
def get_settings_(_: Principal = Depends(require("backup.manage")), session: Session = Depends(get_session)):
    st = service.settings_row(session)
    return {"location": st.location, "daily_time": st.daily_time, "enabled": st.enabled,
            "retention_count": st.retention_count, "include_attachments": st.include_attachments}


@router.put("/backup/settings")
def put_settings(body: SettingsIn, p: Principal = Depends(require("backup.manage")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    st = service.settings_row(session)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(st, k, v)
    session.commit()
    return get_settings_(p, session)


@router.post("/backup/run", response_model=BackupOut)
def run(p: Principal = Depends(require("backup.manage")), meta: RequestMeta = Depends(get_request_meta),
        session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason="نسخة احتياطية يدوية")
    b = service.run_backup(session, "MANUAL", p.user_id)
    session.commit()
    return BackupOut.model_validate(b, from_attributes=True)


@router.get("/backups", response_model=list[BackupOut])
def history(_: Principal = Depends(require("backup.manage")), session: Session = Depends(get_session)):
    return [BackupOut.model_validate(b, from_attributes=True)
            for b in session.scalars(select(Backup).order_by(Backup.started_at.desc()).limit(200))]


@router.post("/backups/{backup_id}/verify", response_model=BackupOut)
def verify(backup_id: uuid.UUID, p: Principal = Depends(require("backup.manage")),
           meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    b = service.get(session, backup_id)
    service.verify(session, b)
    session.commit()
    return BackupOut.model_validate(b, from_attributes=True)


@router.post("/backups/{backup_id}/restore")
def restore(backup_id: uuid.UUID, body: RestoreIn, p: Principal = Depends(require("backup.manage")),
            session: Session = Depends(get_session)):
    session.close()
    return service.restore(backup_id, body.confirm, p.user_id)


@router.get("/health/db")
def health(_: Principal = Depends(require("backup.manage")), session: Session = Depends(get_session)):
    return jsonable(service.health(session))
