import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.core.errors import Forbidden
from app.modules.attachments import service
from app.modules.attachments.models import AttachmentLink
from app.modules.workflow.registry import get_handler

router = APIRouter(prefix="/attachments", tags=["المستندات والمرفقات"])


class AttachmentOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    category: str | None
    description: str | None
    uploaded_by: uuid.UUID
    uploaded_at: datetime
    is_locked: bool


class LinkIn(BaseModel):
    source_type: str
    source_id: uuid.UUID


def _can_see_source(session: Session, p: Principal, source_type: str, source_id: uuid.UUID) -> None:
    h = get_handler(source_type)
    p.require(f"{h.perm_prefix}.view")
    doc = h.get(session, source_id)
    p.require_scope("FISCAL_YEAR", doc.fiscal_year_id)


def _out(a) -> AttachmentOut:
    return AttachmentOut.model_validate(a, from_attributes=True)


@router.post("", response_model=AttachmentOut, status_code=201)
async def upload(file: UploadFile = File(...), category: str | None = Form(default=None),
                 description: str | None = Form(default=None), source_type: str | None = Form(default=None),
                 source_id: uuid.UUID | None = Form(default=None), p: Principal = Depends(require("documents.upload")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    from app.core.config import get_settings
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    begin_write(session, p.user_id, meta)
    if source_type and source_id:
        _can_see_source(session, p, source_type, source_id)
    att = service.save(session, filename=file.filename or "file", data=data, uploaded_by=p.user_id,
                       category=category, description=description)
    if source_type and source_id:
        service.link(session, att, source_type, source_id, p.user_id)
    session.commit()
    return _out(att)


@router.get("", response_model=list[AttachmentOut])
def list_for_source(source_type: str, source_id: uuid.UUID, p: Principal = Depends(require("documents.view")),
                    session: Session = Depends(get_session)):
    _can_see_source(session, p, source_type, source_id)
    return [_out(a) for a, _ in service.for_source(session, source_type, source_id)]


def _can_see_attachment(session: Session, p: Principal, att) -> None:
    from sqlalchemy import select
    links = session.scalars(select(AttachmentLink).where(AttachmentLink.attachment_id == att.id)).all()
    if att.uploaded_by == p.user_id:
        return
    for ln in links:
        try:
            _can_see_source(session, p, ln.source_type, ln.source_id)
            return
        except Forbidden:
            continue
    raise Forbidden("ليست لديك صلاحية على هذا المرفق.", code="PERMISSION_DENIED")


@router.get("/{attachment_id}", response_model=AttachmentOut)
def get_meta(attachment_id: uuid.UUID, p: Principal = Depends(require("documents.view")),
             session: Session = Depends(get_session)):
    att = service.get(session, attachment_id)
    _can_see_attachment(session, p, att)
    return _out(att)


@router.get("/{attachment_id}/download")
def download(attachment_id: uuid.UUID, p: Principal = Depends(require("documents.view")),
             session: Session = Depends(get_session)):
    att = service.get(session, attachment_id)
    _can_see_attachment(session, p, att)
    data = service.read_bytes(att)
    return Response(content=data, media_type=att.mime_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(att.original_filename)}",
        "X-Content-Type-Options": "nosniff"})


@router.post("/{attachment_id}/links", status_code=204)
def add_link(attachment_id: uuid.UUID, body: LinkIn, p: Principal = Depends(require("documents.upload")),
             meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    att = service.get(session, attachment_id)
    _can_see_attachment(session, p, att)
    _can_see_source(session, p, body.source_type, body.source_id)
    service.link(session, att, body.source_type, body.source_id, p.user_id)
    session.commit()


@router.delete("/{attachment_id}/links/{source_type}/{source_id}", status_code=204)
def remove_link(attachment_id: uuid.UUID, source_type: str, source_id: uuid.UUID,
                p: Principal = Depends(require("documents.upload")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    _can_see_source(session, p, source_type, source_id)
    service.unlink(session, service.get(session, attachment_id), source_type, source_id)
    session.commit()
