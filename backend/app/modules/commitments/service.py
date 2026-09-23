"""الحجوزات المبدئية والارتباطات (FR-CM، 03-financial-model §6.3).

- طلب الشراء (PURCHASE_REQUEST) يُرحَّل كحجز مبدئي (RESERVATION).
- أمر الشراء/العقد/الالتزام يُرحَّل كارتباط (COMMITMENT). إذا نشأ من طلب شراء، يُحرَّر الحجز
  في المعاملة نفسها فلا يُخصم المبلغ مرتين.
- القائم يُحسب من القيود المرتبطة بالارتباط (commitment_id)، وليس من حقل قابل للتحرير.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.catalog.models import Supplier
from app.modules.commitments.models import Commitment
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.documents.numbering import next_doc_no
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest
from app.modules.ledger.models import LedgerEntry

SOURCE_TYPE = "commitment"
PR = "PURCHASE_REQUEST"
ZERO = Decimal("0")


def get(session: Session, commitment_id: uuid.UUID) -> Commitment:
    c = session.get(Commitment, commitment_id)
    if c is None:
        raise NotFound("الارتباط غير موجود.")
    return c


def component_of(c: Commitment) -> str:
    return "RESERVATION" if c.commitment_type == PR else "COMMITMENT"


def outstanding(session: Session, c: Commitment) -> Decimal:
    """القائم = مجموع قيود المكوّن المرتبطة بالارتباط (ترحيل − تسييل − إلغاء − إقفال)."""
    v = session.scalar(select(func.coalesce(func.sum(LedgerEntry.direction * LedgerEntry.amount), 0)).where(
        LedgerEntry.commitment_id == c.id, LedgerEntry.component == component_of(c)))
    return Decimal(v)


def paid(session: Session, c: Commitment) -> Decimal:
    v = session.scalar(select(func.coalesce(func.sum(LedgerEntry.direction * LedgerEntry.amount), 0)).where(
        LedgerEntry.commitment_id == c.id, LedgerEntry.txn_type.in_(("COMMITMENT_LIQUIDATION",)),
        LedgerEntry.component == "COMMITMENT"))
    return -Decimal(v)


def cancelled(session: Session, c: Commitment) -> Decimal:
    v = session.scalar(select(func.coalesce(func.sum(LedgerEntry.direction * LedgerEntry.amount), 0)).where(
        LedgerEntry.commitment_id == c.id, LedgerEntry.txn_type.in_(("CANCELLATION", "CLOSING")),
        LedgerEntry.component == component_of(c)))
    return -Decimal(v)


def refresh_status(session: Session, c: Commitment) -> None:
    """حالة الارتباط التجارية بعد أي حركة (FR-CM-02)."""
    if c.status != "POSTED":
        return
    out = outstanding(session, c)
    if c.commitment_type == PR:
        converted = session.scalar(select(Commitment.id).where(Commitment.parent_id == c.id,
                                                                Commitment.status == "POSTED"))
        c.commitment_status = "CLOSED" if converted else ("APPROVED" if out > 0 else "CANCELLED")
        return
    p = paid(session, c)
    if out == 0:
        c.commitment_status = "FULLY_PAID" if p > 0 and cancelled(session, c) == 0 else (
            "CLOSED" if p > 0 else "CANCELLED")
    else:
        c.commitment_status = "PARTIALLY_PAID" if p > 0 else "APPROVED"
    session.flush()


def _validate(session: Session, principal: Principal, c: Commitment) -> None:
    if c.supplier_id and session.get(Supplier, c.supplier_id) is None:
        raise ValidationFailed("المورد غير موجود.", code="UNKNOWN_SUPPLIER")
    if c.parent_id:
        parent = get(session, c.parent_id)
        if parent.commitment_type != PR:
            raise ValidationFailed("يمكن التحويل من طلب شراء فقط.", code="INVALID_PARENT")
        if c.commitment_type == PR:
            raise ValidationFailed("لا يُحوَّل طلب شراء إلى طلب شراء.", code="INVALID_PARENT")
        if parent.status != "POSTED" or parent.commitment_status != "APPROVED":
            raise Conflict("طلب الشراء غير معتمد أو حُوِّل مسبقًا.", code="PARENT_NOT_OPEN")
        active_child = session.scalar(select(Commitment.id).where(
            Commitment.parent_id == parent.id, Commitment.id != c.id,
            Commitment.status.not_in(("CANCELLED", "REJECTED"))))
        if active_child:
            raise Conflict("طلب الشراء له أمر شراء قائم (حُوِّل مسبقًا).", code="ALREADY_CONVERTED")
        if parent.budget_line_id != c.budget_line_id:
            raise ValidationFailed("أمر الشراء يجب أن يكون على بند طلب الشراء نفسه.", code="PARENT_LINE_MISMATCH")


def create_draft(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, commitment_type: str,
                 commitment_date: date, entity_id: uuid.UUID, item_id: uuid.UUID, amount: Decimal, description: str,
                 supplier_id: uuid.UUID | None = None, parent_id: uuid.UUID | None = None,
                 reference: str | None = None, expected_completion: date | None = None,
                 commitment_no: str | None = None) -> Commitment:
    fy = year_for_new_document(session, fiscal_year_id, commitment_date, principal)
    line = engine.get_or_create_line(session, fy.id, entity_id, item_id)
    require_line_scope(session, principal, line)
    c = Commitment(id=uuid.uuid4(), fiscal_year_id=fy.id, commitment_type=commitment_type,
                   commitment_no=commitment_no or next_doc_no(session, fy.id, fy.year, commitment_type),
                   commitment_date=commitment_date, budget_line_id=line.id, supplier_id=supplier_id,
                   parent_id=parent_id, amount=amount, description=description.strip(), reference=reference,
                   expected_completion=expected_completion, created_by=principal.user_id)
    _validate(session, principal, c)
    if session.scalar(select(Commitment.id).where(Commitment.fiscal_year_id == fy.id,
                                                  Commitment.commitment_type == commitment_type,
                                                  Commitment.commitment_no == c.commitment_no)):
        raise Conflict("رقم المستند مستخدم مسبقًا لهذا النوع في السنة.", code="DUPLICATE_DOC_NO")
    session.add(c)
    session.flush()
    return c


def convert(session: Session, principal: Principal, pr: Commitment, *, commitment_type: str, amount: Decimal,
            commitment_date: date, description: str, supplier_id: uuid.UUID | None, reference: str | None) -> Commitment:
    """FR-CM-04: تحويل طلب شراء معتمد إلى أمر شراء/عقد (مسودة تمر بدورة الموافقات)."""
    from app.modules.ledger.models import BudgetLine
    line = session.get(BudgetLine, pr.budget_line_id)
    return create_draft(session, principal, fiscal_year_id=pr.fiscal_year_id, commitment_type=commitment_type,
                        commitment_date=commitment_date, entity_id=line.entity_id, item_id=line.item_id,
                        amount=amount, description=description, supplier_id=supplier_id, parent_id=pr.id,
                        reference=reference)


def update_draft(session: Session, principal: Principal, c: Commitment, changes: dict,
                 expected_version: int | None) -> Commitment:
    ensure_editable(c, expected_version)
    if c.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "commitment_date" in changes:
        year_for_new_document(session, c.fiscal_year_id, changes["commitment_date"], principal)
    for k in ("commitment_date", "amount", "description", "supplier_id", "reference", "expected_completion"):
        if k in changes:
            setattr(c, k, changes[k])
    _validate(session, principal, c)
    c.row_version += 1
    session.flush()
    return c


def build_posting(session: Session, c: Commitment, posted_by: uuid.UUID, approved_by: uuid.UUID | None) -> PostingRequest:
    specs = []
    if c.parent_id:
        parent = get(session, c.parent_id)
        held = outstanding(session, parent)
        if held > 0:
            specs.append(EntrySpec(parent.budget_line_id, "RESERVATION_RELEASE", "RESERVATION", -1, held,
                                   commitment_id=parent.id, description=f"تحرير حجز {parent.commitment_no} ← {c.commitment_no}"))
    txn = "PRE_COMMITMENT" if c.commitment_type == PR else "COMMITMENT"
    specs.append(EntrySpec(c.budget_line_id, txn, component_of(c), 1, c.amount, commitment_id=c.id,
                           description=c.description))
    return PostingRequest(fiscal_year_id=c.fiscal_year_id, entry_date=c.commitment_date, source_type=SOURCE_TYPE,
                          source_id=c.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=c.commitment_no)


def post(session: Session, c: Commitment, actor: uuid.UUID, override_grant_id: uuid.UUID | None = None,
         *, historical_exception: bool = False, date_is_estimated: bool = False) -> engine.PostingResult:
    if c.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("الارتباط مرحّل أو في حالة نهائية.", code="INVALID_STATE")
    _validate(session, None, c)
    req = build_posting(session, c, actor, actor)
    req.override_grant_id = override_grant_id
    req.historical_exception = historical_exception
    req.date_is_estimated = date_is_estimated
    result = engine.post(session, req)
    c.status, c.posted_by, c.posted_at = "POSTED", actor, datetime.now(UTC)
    c.commitment_status = "APPROVED"
    c.row_version += 1
    session.flush()
    if c.parent_id:
        refresh_status(session, get(session, c.parent_id))
    return result


def on_workflow_change(session: Session, c: Commitment) -> None:
    """حالة الارتباط التجارية أثناء دورة الموافقة."""
    if c.status in ("SUBMITTED", "IN_REVIEW"):
        c.commitment_status = "PENDING_APPROVAL"
    elif c.status in ("DRAFT", "RETURNED"):
        c.commitment_status = "DRAFT"
    elif c.status in ("CANCELLED", "REJECTED"):
        c.commitment_status = "CANCELLED"
