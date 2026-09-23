"""سجل التدقيق (11-audit): استعراض وتاريخ سجل والتحقق من سلامة سلسلة Hash."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, require

router = APIRouter(prefix="/audit-log", tags=["التدقيق"])


class AuditRow(BaseModel):
    id: int
    occurred_at: datetime
    user_id: str | None
    user_name: str | None
    ip: str | None
    user_agent: str | None
    action: str
    table_name: str | None
    record_id: str | None
    changed_fields: list[str] | None
    old_values: dict | None
    new_values: dict | None
    reason: str | None


class AuditPage(BaseModel):
    items: list[AuditRow]
    next_before_id: int | None


_BASE = """SELECT a.id, a.occurred_at, a.user_id::text AS user_id, u.full_name AS user_name, a.ip, a.user_agent,
                  a.action, a.table_name, a.record_id, a.changed_fields, a.old_values, a.new_values, a.reason
           FROM audit_log a LEFT JOIN users u ON u.id = a.user_id"""


@router.get("", response_model=AuditPage)
def list_log(user_id: str | None = None, table_name: str | None = Query(default=None, max_length=63),
             record_id: str | None = Query(default=None, max_length=64), action: str | None = Query(default=None, max_length=40),
             date_from: date | None = None, date_to: date | None = None, before_id: int | None = None,
             limit: int = Query(default=100, ge=1, le=500), _: Principal = Depends(require("audit.view")),
             session: Session = Depends(get_session)):
    """ترقيم بالمؤشر (Cursor) لأن السجل ينمو بلا حد (08-api §4)."""
    rows = session.execute(text(_BASE + """
        WHERE (CAST(:uid AS uuid) IS NULL OR a.user_id = CAST(:uid AS uuid))
          AND (CAST(:tbl AS text) IS NULL OR a.table_name = :tbl)
          AND (CAST(:rid AS text) IS NULL OR a.record_id = :rid)
          AND (CAST(:act AS text) IS NULL OR a.action = :act)
          AND (CAST(:df AS date) IS NULL OR a.occurred_at >= CAST(:df AS date))
          AND (CAST(:dt AS date) IS NULL OR a.occurred_at < CAST(:dt AS date) + 1)
          AND (CAST(:before AS bigint) IS NULL OR a.id < :before)
        ORDER BY a.id DESC LIMIT :limit"""), {"uid": user_id, "tbl": table_name, "rid": record_id, "act": action,
                                              "df": date_from, "dt": date_to, "before": before_id, "limit": limit})
    items = [AuditRow(**r._mapping) for r in rows]
    return AuditPage(items=items, next_before_id=items[-1].id if len(items) == limit else None)


@router.get("/record/{table_name}/{record_id}", response_model=list[AuditRow])
def record_history(table_name: str, record_id: str, _: Principal = Depends(require("audit.view")),
                   session: Session = Depends(get_session)):
    rows = session.execute(text(_BASE + " WHERE a.table_name = :t AND a.record_id = :r ORDER BY a.id"),
                           {"t": table_name, "r": record_id})
    return [AuditRow(**r._mapping) for r in rows]


@router.post("/verify-chain")
def verify_chain(_: Principal = Depends(require("audit.verify")), session: Session = Depends(get_session)):
    broken, checked = session.execute(text("SELECT * FROM audit_verify_chain()")).one()
    last = session.execute(text("SELECT id, row_hash FROM audit_log ORDER BY id DESC LIMIT 1")).one()
    return {"ok": broken is None, "broken_at_id": broken, "checked": checked, "last_id": last.id,
            "last_hash": last.row_hash, "verified_at": datetime.now().astimezone().isoformat()}
