"""التصحيح دون حذف (FR-RV، 11-audit §4):

- REVERSAL: قيد عكسي كامل لمستند مرحّل (والمناقلة تُعكس كاملة).
- ADJUSTMENT: تسوية بمبلغ محدد على مكوّن محدد (الاعتماد أو المفوَّض أو الفعلي).
- COMMITMENT_CANCELLATION: إلغاء الجزء غير المسدد من ارتباط أو حجز معتمد.

كلها مستندات تمر بدورة موافقة (مسار adjustment) وسبب إلزامي.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.adjustments.models import Adjustment, AdjustmentLine
from app.modules.commitments import service as commitments
from app.modules.commitments.models import Commitment
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.documents.numbering import next_doc_no
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest
from app.modules.ledger.models import LedgerEntry
from app.modules.workflow.registry import get_handler

SOURCE_TYPE = "adjustment"
ZERO = Decimal("0")


def get(session: Session, adjustment_id: uuid.UUID) -> Adjustment:
    a = session.get(Adjustment, adjustment_id)
    if a is None:
        raise NotFound("مستند التسوية غير موجود.")
    return a


def lines_of(session: Session, adjustment_id: uuid.UUID) -> list[AdjustmentLine]:
    return list(session.scalars(select(AdjustmentLine).where(AdjustmentLine.adjustment_id == adjustment_id)))


def _source_entries(session: Session, a: Adjustment) -> list[LedgerEntry]:
    return list(session.scalars(select(LedgerEntry).where(LedgerEntry.source_type == a.reverses_source_type,
                                                          LedgerEntry.source_id == a.reverses_source_id,
                                                          LedgerEntry.txn_type != "REVERSAL")))


def amount(session: Session, a: Adjustment) -> Decimal:
    if a.kind == "REVERSAL":
        return sum((e.amount for e in _source_entries(session, a)), ZERO)
    if a.kind == "COMMITMENT_CANCELLATION":
        return a.cancel_amount or ZERO
    return sum((ln.amount for ln in lines_of(session, a.id)), ZERO)


def line_ids(session: Session, a: Adjustment) -> list[uuid.UUID]:
    if a.kind == "REVERSAL":
        return list({e.budget_line_id for e in _source_entries(session, a)})
    if a.kind == "COMMITMENT_CANCELLATION":
        return [commitments.get(session, a.commitment_id).budget_line_id]
    return [ln.budget_line_id for ln in lines_of(session, a.id)]


def _check_reversible(session: Session, a: Adjustment) -> None:
    if a.reverses_source_type == SOURCE_TYPE:
        src = get(session, a.reverses_source_id)
        if src.kind == "REVERSAL":
            raise ValidationFailed("لا يُعكس قيد عكسي؛ أنشئ المستند الصحيح من جديد.", code="CANNOT_REVERSE_REVERSAL")
    handler = get_handler(a.reverses_source_type)
    doc = handler.get(session, a.reverses_source_id)
    if doc.fiscal_year_id != a.fiscal_year_id:
        raise ValidationFailed("القيد العكسي في السنة المالية للمستند الأصلي نفسها.", code="YEAR_MISMATCH")
    if doc.status != "POSTED":
        raise Conflict("يُعكس المستند المرحّل فقط.", code="NOT_POSTED", details={"status": doc.status})
    if a.reverses_source_type == commitments.SOURCE_TYPE:
        c = doc
        if c.commitment_type != commitments.PR and commitments.paid(session, c) > 0:
            raise Conflict("الارتباط عليه مدفوعات؛ اعكس المصروفات أولًا أو ألغِ الجزء المتبقي.",
                           code="COMMITMENT_HAS_PAYMENTS")
        child = session.scalar(select(Commitment.id).where(Commitment.parent_id == c.id, Commitment.status == "POSTED"))
        if child:
            raise Conflict("طلب الشراء حُوِّل إلى أمر شراء مرحّل؛ اعكس أمر الشراء أولًا.", code="HAS_CHILD")


def create(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, kind: str, adjustment_date: date,
           reason: str, reverses_source_type: str | None = None, reverses_source_id: uuid.UUID | None = None,
           commitment_id: uuid.UUID | None = None, cancel_amount: Decimal | None = None,
           lines: list[dict] | None = None) -> Adjustment:
    fy = year_for_new_document(session, fiscal_year_id, adjustment_date, principal)
    doc_type = "REVERSAL" if kind == "REVERSAL" else "ADJUSTMENT"
    a = Adjustment(id=uuid.uuid4(), fiscal_year_id=fy.id, kind=kind,
                   adjustment_no=next_doc_no(session, fy.id, fy.year, doc_type), adjustment_date=adjustment_date,
                   reason=reason.strip(), reverses_source_type=reverses_source_type,
                   reverses_source_id=reverses_source_id, commitment_id=commitment_id, cancel_amount=cancel_amount,
                   created_by=principal.user_id)
    if kind == "REVERSAL":
        if not (reverses_source_type and reverses_source_id):
            raise ValidationFailed("حدد المستند المراد عكسه.", code="SOURCE_REQUIRED")
        _check_reversible(session, a)
        dup = session.scalar(select(Adjustment.id).where(
            Adjustment.kind == "REVERSAL", Adjustment.reverses_source_type == reverses_source_type,
            Adjustment.reverses_source_id == reverses_source_id, Adjustment.status.not_in(("CANCELLED", "REJECTED"))))
        if dup:
            raise Conflict("يوجد قيد عكسي قائم لهذا المستند.", code="REVERSAL_EXISTS")
    elif kind == "COMMITMENT_CANCELLATION":
        c = commitments.get(session, commitment_id) if commitment_id else None
        if c is None or c.status != "POSTED":
            raise ValidationFailed("حدد ارتباطًا معتمدًا.", code="COMMITMENT_REQUIRED")
        out = commitments.outstanding(session, c)
        a.cancel_amount = cancel_amount or out
        if a.cancel_amount <= 0 or a.cancel_amount > out:
            raise ValidationFailed(f"مبلغ الإلغاء يجب أن يكون بين 0 والقائم ({out}).", code="INVALID_AMOUNT")
    session.add(a)
    session.flush()
    if kind == "ADJUSTMENT":
        _set_lines(session, principal, a, lines or [])
    for lid in line_ids(session, a):
        from app.modules.ledger.models import BudgetLine
        require_line_scope(session, principal, session.get(BudgetLine, lid))
    return a


def _set_lines(session: Session, principal: Principal, a: Adjustment, lines: list[dict]) -> None:
    if not lines:
        raise ValidationFailed("التسوية تتطلب سطرًا واحدًا على الأقل.", code="NO_LINES")
    session.execute(delete(AdjustmentLine).where(AdjustmentLine.adjustment_id == a.id))
    for ln in lines:
        bl = engine.get_or_create_line(session, a.fiscal_year_id, ln["entity_id"], ln["item_id"])
        require_line_scope(session, principal, bl)
        session.add(AdjustmentLine(adjustment_id=a.id, budget_line_id=bl.id, component=ln["component"],
                                   direction=ln["direction"], amount=ln["amount"]))
    session.flush()


def update_draft(session: Session, principal: Principal, a: Adjustment, changes: dict,
                 expected_version: int | None) -> Adjustment:
    ensure_editable(a, expected_version)
    if a.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "reason" in changes:
        a.reason = changes["reason"]
    if "adjustment_date" in changes:
        year_for_new_document(session, a.fiscal_year_id, changes["adjustment_date"], principal)
        a.adjustment_date = changes["adjustment_date"]
    if "lines" in changes and a.kind == "ADJUSTMENT":
        _set_lines(session, principal, a, changes["lines"])
    a.row_version += 1
    session.flush()
    return a


def build_posting(session: Session, a: Adjustment, posted_by: uuid.UUID, approved_by: uuid.UUID | None) -> PostingRequest:
    if a.kind == "REVERSAL":
        specs = engine.reversal_specs(session, a.reverses_source_type, a.reverses_source_id, a.reason)
    elif a.kind == "COMMITMENT_CANCELLATION":
        c = commitments.get(session, a.commitment_id)
        specs = [EntrySpec(c.budget_line_id, "CANCELLATION", commitments.component_of(c), -1, a.cancel_amount,
                           commitment_id=c.id, description=f"إلغاء {c.commitment_no}: {a.reason}")]
    else:
        specs = [EntrySpec(ln.budget_line_id, "ADJUSTMENT", ln.component, ln.direction, ln.amount,
                           source_line_id=ln.id, description=a.reason) for ln in lines_of(session, a.id)]
    return PostingRequest(fiscal_year_id=a.fiscal_year_id, entry_date=a.adjustment_date, source_type=SOURCE_TYPE,
                          source_id=a.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=a.adjustment_no)


def post(session: Session, a: Adjustment, actor: uuid.UUID, override_grant_id: uuid.UUID | None = None):
    if a.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("المستند مرحّل أو في حالة نهائية.", code="INVALID_STATE")
    if a.kind == "REVERSAL":
        _check_reversible(session, a)
    if a.kind == "COMMITMENT_CANCELLATION":
        out = commitments.outstanding(session, commitments.get(session, a.commitment_id))
        if a.cancel_amount > out:
            raise Conflict(f"مبلغ الإلغاء أكبر من القائم حاليًا ({out}).", code="INVALID_AMOUNT")
    req = build_posting(session, a, actor, actor)
    req.override_grant_id = override_grant_id
    result = engine.post(session, req)
    a.status, a.posted_by, a.posted_at = "POSTED", actor, datetime.now(UTC)
    a.row_version += 1
    session.flush()
    if a.kind == "REVERSAL":
        doc = get_handler(a.reverses_source_type).get(session, a.reverses_source_id)
        doc.status = "REVERSED"
        doc.row_version += 1
        session.flush()
    affected = {e.commitment_id for e in result.entries if e.commitment_id}
    for cid in affected:
        commitments.refresh_status(session, commitments.get(session, cid))
    return result


def reversed_total(session: Session, source_type: str, source_id: uuid.UUID) -> Decimal:
    return Decimal(session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
        LedgerEntry.txn_type == "REVERSAL", LedgerEntry.reversal_of_id.in_(
            select(LedgerEntry.id).where(LedgerEntry.source_type == source_type, LedgerEntry.source_id == source_id)))))
