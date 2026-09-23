"""المناقلات: مستند مستقل، يولّد عند ترحيله زوجًا متوازنًا لكل سطر (FR-TR)."""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.documents.numbering import next_doc_no
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest
from app.modules.transfers.models import Transfer, TransferLine

SOURCE_TYPE = "transfer"


def get(session: Session, transfer_id: uuid.UUID) -> Transfer:
    t = session.get(Transfer, transfer_id)
    if t is None:
        raise NotFound("المناقلة غير موجودة.")
    return t


def lines_of(session: Session, transfer_id: uuid.UUID) -> list[TransferLine]:
    return list(session.scalars(select(TransferLine).where(TransferLine.transfer_id == transfer_id)))


def total(session: Session, t: Transfer) -> Decimal:
    return sum((ln.amount for ln in lines_of(session, t.id)), Decimal("0"))


def _set_lines(session: Session, principal: Principal, t: Transfer, lines: list[dict]) -> None:
    if not lines:
        raise ValidationFailed("المناقلة يجب أن تحتوي سطرًا واحدًا على الأقل.", code="NO_LINES")
    session.execute(delete(TransferLine).where(TransferLine.transfer_id == t.id))
    for ln in lines:
        src = engine.get_or_create_line(session, t.fiscal_year_id, ln["from_entity_id"], ln["from_item_id"])
        dst = engine.get_or_create_line(session, t.fiscal_year_id, ln["to_entity_id"], ln["to_item_id"])
        if src.id == dst.id:
            raise ValidationFailed("لا يمكن المناقلة إلى البند نفسه.", code="SAME_LINE")
        require_line_scope(session, principal, src)
        require_line_scope(session, principal, dst)
        session.add(TransferLine(transfer_id=t.id, from_line_id=src.id, to_line_id=dst.id, amount=ln["amount"]))
    session.flush()


def create_draft(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, transfer_date: date,
                 reason: str, approval_no: str | None, lines: list[dict], transfer_no: str | None = None) -> Transfer:
    fy = year_for_new_document(session, fiscal_year_id, transfer_date, principal)
    t = Transfer(id=uuid.uuid4(), fiscal_year_id=fy.id,
                 transfer_no=transfer_no or next_doc_no(session, fy.id, fy.year, SOURCE_TYPE),
                 transfer_date=transfer_date, reason=reason.strip(), approval_no=approval_no,
                 created_by=principal.user_id)
    if session.scalar(select(Transfer.id).where(Transfer.fiscal_year_id == fy.id,
                                                Transfer.transfer_no == t.transfer_no)):
        raise Conflict("رقم المناقلة مستخدم مسبقًا في السنة.", code="DUPLICATE_DOC_NO")
    session.add(t)
    session.flush()
    _set_lines(session, principal, t, lines)
    return t


def update_draft(session: Session, principal: Principal, t: Transfer, changes: dict,
                 expected_version: int | None) -> Transfer:
    ensure_editable(t, expected_version)
    if t.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "transfer_date" in changes:
        year_for_new_document(session, t.fiscal_year_id, changes["transfer_date"], principal)
    for k in ("transfer_date", "reason", "approval_no"):
        if k in changes:
            setattr(t, k, changes[k])
    if "lines" in changes:
        _set_lines(session, principal, t, changes["lines"])
    t.row_version += 1
    session.flush()
    return t


def build_posting(session: Session, t: Transfer, posted_by: uuid.UUID, approved_by: uuid.UUID | None) -> PostingRequest:
    specs = []
    for ln in lines_of(session, t.id):
        group = uuid.uuid5(ln.id, "transfer")   # ثابت لكل سطر: زوج واحد لا يُفصل
        specs += [EntrySpec(ln.from_line_id, "TRANSFER_OUT", "TRANSFER_OUT", 1, ln.amount, source_line_id=ln.id,
                            transfer_group_id=group, description=f"مناقلة {t.transfer_no}: {t.reason}"),
                  EntrySpec(ln.to_line_id, "TRANSFER_IN", "TRANSFER_IN", 1, ln.amount, source_line_id=ln.id,
                            transfer_group_id=group, description=f"مناقلة {t.transfer_no}: {t.reason}")]
    return PostingRequest(fiscal_year_id=t.fiscal_year_id, entry_date=t.transfer_date, source_type=SOURCE_TYPE,
                          source_id=t.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=t.transfer_no)


def post(session: Session, t: Transfer, actor: uuid.UUID, override_grant_id: uuid.UUID | None = None,
         *, historical_exception: bool = False, date_is_estimated: bool = False) -> engine.PostingResult:
    if t.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("المناقلة مرحّلة أو في حالة نهائية.", code="INVALID_STATE")
    req = build_posting(session, t, actor, actor)
    req.override_grant_id = override_grant_id
    req.historical_exception = historical_exception
    req.date_is_estimated = date_is_estimated
    result = engine.post(session, req)
    t.status, t.posted_by, t.posted_at, t.approved_by = "POSTED", actor, datetime.now(UTC), actor
    t.row_version += 1
    session.flush()
    return result
