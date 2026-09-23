import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.core.errors import ValidationFailed
from app.modules.reports import (
    definitions,  # noqa: F401  (تسجيل التقارير)
    render,
)
from app.modules.reports.engine import REGISTRY, get_def, jsonable_row

router = APIRouter(prefix="/reports", tags=["التقارير"])


class ReportInfo(BaseModel):
    code: str
    title: str
    description: str
    params: list[str]


def _run(session: Session, p: Principal, code: str, params: dict):
    d = get_def(code)
    p.require(d.permission)
    return d.run(session, p, params)


@router.get("", response_model=list[ReportInfo])
def catalog(p: Principal = Depends(require("reports.view"))):
    return [ReportInfo(code=d.code, title=d.title, description=d.description, params=list(d.params))
            for d in REGISTRY.values() if p.has(d.permission)]


@router.post("/{code}/run")
def run(code: str, params: dict, p: Principal = Depends(require("reports.view")),
        session: Session = Depends(get_session)):
    data = _run(session, p, code, params)
    return {"code": data.code, "title": data.title, "subtitle": data.subtitle, "notes": data.notes,
            "columns": [c.__dict__ for c in data.columns], "rows": [jsonable_row(r) for r in data.rows],
            "totals": jsonable_row(data.totals), "fingerprint": data.fingerprint()}


@router.post("/{code}/export")
def export(code: str, params: dict, format: str = Query(pattern="^(pdf|xlsx|html)$"),
           p: Principal = Depends(require("reports.export")), meta: RequestMeta = Depends(get_request_meta),
           session: Session = Depends(get_session)):
    data = _run(session, p, code, params)
    if len(data.rows) > 20000:
        raise ValidationFailed("التقرير كبير جدًا للتصدير المباشر؛ ضيّق الفلاتر.", code="REPORT_TOO_LARGE")
    begin_write(session, p.user_id, meta)
    session.execute(text("SELECT audit_append('EXPORT', 'reports', :c, NULL, CAST(:d AS jsonb))"),
                    {"c": code, "d": json.dumps({"format": format, "params": params, "rows": len(data.rows),
                                                 "fingerprint": data.fingerprint()}, default=str, ensure_ascii=False)})
    session.commit()
    who = f"{p.full_name} ({p.username})"
    if format == "html":
        return HTMLResponse(render.to_html(data, generated_by=who))
    body, mime, ext = ((render.to_pdf(data, generated_by=who), "application/pdf", "pdf") if format == "pdf" else
                       (render.to_xlsx(data, generated_by=who),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"))
    name = quote(f"{data.code} {data.title}.{ext}")
    return Response(body, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})
