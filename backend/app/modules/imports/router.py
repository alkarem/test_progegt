import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.imports import service
from app.modules.imports.models import ImportBatch
from app.modules.reports import render
from app.modules.reports.engine import Column, ReportData
from app.shared.money import jsonable

router = APIRouter(prefix="/imports", tags=["استيراد Excel"])


class BatchOut(BaseModel):
    id: uuid.UUID
    filename: str
    sha256: str
    fiscal_year_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    status: str
    decisions: dict
    stats: dict | None
    created_by: uuid.UUID
    created_at: datetime
    imported_by: uuid.UUID | None
    imported_at: datetime | None


class IssueOut(BaseModel):
    id: uuid.UUID
    severity: str
    code: str
    location: str | None
    message: str
    decision: str | None
    blocking: bool
    details: dict | None


def _out(b: ImportBatch) -> BatchOut:
    return BatchOut.model_validate(b, from_attributes=True)


def _batch(session: Session, p: Principal, batch_id: uuid.UUID) -> ImportBatch:
    b = service.get(session, batch_id)
    p.require_scope("FISCAL_YEAR", b.fiscal_year_id)
    return b


@router.post("", response_model=BatchOut, status_code=201)
async def upload(file: UploadFile = File(...), fiscal_year_id: uuid.UUID = Form(...),
                 entity_id: uuid.UUID | None = Form(default=None), p: Principal = Depends(require("imports.prepare")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    begin_write(session, p.user_id, meta, reason="رفع ملف استيراد")
    b = service.upload(session, p, file.filename or "import.xlsx", data, fiscal_year_id, entity_id)
    session.commit()
    return _out(b)


@router.get("", response_model=list[BatchOut])
def list_batches(p: Principal = Depends(require("imports.prepare")), session: Session = Depends(get_session)):
    return [_out(b) for b in session.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()))]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: uuid.UUID, p: Principal = Depends(require("imports.prepare")),
              session: Session = Depends(get_session)):
    return _out(_batch(session, p, batch_id))


def _step(name: str):
    def endpoint(batch_id: uuid.UUID, p: Principal = Depends(require("imports.prepare")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
        begin_write(session, p.user_id, meta, reason=f"استيراد: {name}")
        b = _batch(session, p, batch_id)
        result = {"analyze": service.analyze, "validate": service.validate}[name](session, b)
        session.commit()
        return jsonable(result)
    return endpoint


router.add_api_route("/{batch_id}/analyze", _step("analyze"), methods=["POST"], name="import_analyze")
router.add_api_route("/{batch_id}/validate", _step("validate"), methods=["POST"], name="import_validate")


@router.get("/{batch_id}/issues", response_model=list[IssueOut])
def issues(batch_id: uuid.UUID, p: Principal = Depends(require("imports.prepare")),
           session: Session = Depends(get_session)):
    return [IssueOut.model_validate(i, from_attributes=True) for i in service.issues(session, _batch(session, p, batch_id))]


@router.put("/{batch_id}/decisions")
def decisions(batch_id: uuid.UUID, body: dict, p: Principal = Depends(require("imports.prepare")),
              meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason="قرارات الاستيراد")
    result = service.set_decisions(session, _batch(session, p, batch_id), body)
    session.commit()
    return result


@router.get("/{batch_id}/preview")
def preview(batch_id: uuid.UUID, p: Principal = Depends(require("imports.prepare")),
            session: Session = Depends(get_session)):
    return service.preview(session, _batch(session, p, batch_id))


@router.post("/{batch_id}/commit")
def commit(batch_id: uuid.UUID, p: Principal = Depends(require("imports.commit")),
           meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason=f"تنفيذ دفعة الاستيراد {batch_id}")
    b = session.get(ImportBatch, batch_id, with_for_update=True)
    created = service.commit(session, p, b if b else service.get(session, batch_id))
    session.commit()
    return {"status": "IMPORTED", "created": created}


@router.post("/{batch_id}/discard", response_model=BatchOut)
def discard(batch_id: uuid.UUID, p: Principal = Depends(require("imports.prepare")),
            meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    b = _batch(session, p, batch_id)
    service.discard(session, b)
    session.commit()
    return _out(b)


SEV = {"CRITICAL": "حرج", "HIGH": "عالٍ", "WARNING": "متوسط", "INFO": "منخفض"}


@router.get("/{batch_id}/report")
def dq_report(batch_id: uuid.UUID, format: str = Query(default="json", pattern="^(json|pdf|xlsx)$"),
              p: Principal = Depends(require("imports.prepare")), session: Session = Depends(get_session)):
    """تقرير جودة البيانات (10-excel-import §5): المشاكل + المطابقة لكل بند بين Excel والمعاد حسابه."""
    b = _batch(session, p, batch_id)
    pv = service.preview(session, b)
    issues_rows = [{"severity": SEV[i.severity], "code": i.code, "location": i.location or "", "message": i.message,
                    "decision": i.decision or ""} for i in service.issues(session, b)]
    if format == "json":
        return {"batch": _out(b).model_dump(mode="json"), "issues": issues_rows, "reconciliation": pv["positions"],
                "totals": pv["totals"]}
    from decimal import Decimal
    recon = [{**r, **{k: Decimal(r[k]) if r.get(k) not in (None, "") else None
                      for k in ("allocation", "transfer_in", "transfer_out", "actual", "book_balance", "excel_balance",
                                "difference")}} for r in pv["positions"]]
    data = ReportData("RPT-DQ", "تقرير جودة البيانات — مطابقة Excel مع الحركات", [
        Column("item_code", "البند"), Column("allocation", "التفويض", "money", True),
        Column("transfer_in", "وارد", "money", True), Column("transfer_out", "صادر", "money", True),
        Column("actual", "الفعلي", "money", True), Column("book_balance", "الرصيد المعاد حسابه", "money", True),
        Column("excel_balance", "رصيد Excel", "money", True), Column("difference", "الفرق", "money", True)],
        recon, {"file": b.filename, "sha256": b.sha256[:16]},
        subtitle=f"الملف: {b.filename}", notes=[f"[{r['severity']}] {r['code']} {r['location']}: {r['message']}"
                                                for r in issues_rows])
    who = f"{p.full_name} ({p.username})"
    body, mime, ext = ((render.to_pdf(data, generated_by=who), "application/pdf", "pdf") if format == "pdf" else
                       (render.to_xlsx(data, generated_by=who),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"))
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote('تقرير جودة البيانات.' + ext)}"})
