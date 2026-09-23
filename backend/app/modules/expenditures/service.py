"""المصروف الفعلي (FR-EX). عند الترحيل: قيد فعلي، وإذا ارتبط بارتباط يُسيَّل الارتباط بمقدار
min(المبلغ، القائم)، وما يزيد يخضع لفحص الرصيد كصرف جديد (03-financial-model §6.3)."""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.catalog.models import Supplier
from app.modules.commitments import service as commitments
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.documents.numbering import next_doc_no
from app.modules.expenditures.models import Expenditure
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest

SOURCE_TYPE = "expenditure"
INACTIVE = ("CANCELLED", "REJECTED")


def get(session: Session, expenditure_id: uuid.UUID) -> Expenditure:
    e = session.get(Expenditure, expenditure_id)
    if e is None:
        raise NotFound("المصروف غير موجود.")
    return e


def _validate(session: Session, e: Expenditure) -> None:
    if e.supplier_id and session.get(Supplier, e.supplier_id) is None:
        raise ValidationFailed("المورد/المستفيد غير موجود.", code="UNKNOWN_SUPPLIER")
    if e.payment_method == "CHEQUE" and not (e.cheque_no or "").strip():
        raise ValidationFailed("رقم الشيك مطلوب عند الدفع بشيك.", code="CHEQUE_NO_REQUIRED")
    if e.commitment_id:
        c = commitments.get(session, e.commitment_id)
        if c.commitment_type == commitments.PR:
            raise ValidationFailed("لا يُصرف على طلب شراء مباشرة؛ حوّله إلى أمر شراء أو عقد.",
                                   code="CANNOT_PAY_PURCHASE_REQUEST")
        if c.status != "POSTED" or c.commitment_status in ("CANCELLED", "CLOSED", "FULLY_PAID"):
            raise Conflict("الارتباط غير معتمد أو مغلق.", code="COMMITMENT_NOT_OPEN")
        if c.budget_line_id != e.budget_line_id:
            raise ValidationFailed("المصروف يجب أن يكون على بند الارتباط نفسه.", code="COMMITMENT_LINE_MISMATCH")
    dup = session.scalar(select(Expenditure.id).where(
        Expenditure.fiscal_year_id == e.fiscal_year_id, Expenditure.document_type == e.document_type,
        Expenditure.document_no == e.document_no, Expenditure.id != e.id, Expenditure.status.not_in(INACTIVE)))
    if dup:
        raise Conflict(f"رقم المستند {e.document_no} مسجل مسبقًا في السنة.", code="DUPLICATE_DOCUMENT_NO")


def similar(session: Session, e: Expenditure) -> list[Expenditure]:
    """مصروفات محتمل تكرارها: المستفيد نفسه والمبلغ نفسه خلال ±3 أيام (FR-EX-05، 12-ai §3)."""
    if not e.supplier_id:
        return []
    return list(session.scalars(select(Expenditure).where(
        Expenditure.id != e.id, Expenditure.supplier_id == e.supplier_id, Expenditure.amount == e.amount,
        Expenditure.status.not_in(INACTIVE),
        Expenditure.expenditure_date.between(e.expenditure_date - timedelta(days=3),
                                             e.expenditure_date + timedelta(days=3)))))


def create_draft(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, expenditure_date: date,
                 entity_id: uuid.UUID, item_id: uuid.UUID, amount: Decimal, payment_method: str, description: str,
                 document_no: str | None = None, document_type: str = "PAYMENT_VOUCHER",
                 supplier_id: uuid.UUID | None = None, commitment_id: uuid.UUID | None = None,
                 expense_type: str | None = None, payment_order_no: str | None = None, cheque_no: str | None = None,
                 notes: str | None = None) -> Expenditure:
    fy = year_for_new_document(session, fiscal_year_id, expenditure_date, principal)
    line = engine.get_or_create_line(session, fy.id, entity_id, item_id)
    require_line_scope(session, principal, line)
    e = Expenditure(id=uuid.uuid4(), fiscal_year_id=fy.id,
                    document_no=(document_no or next_doc_no(session, fy.id, fy.year, SOURCE_TYPE)).strip(),
                    document_type=document_type, expenditure_date=expenditure_date, budget_line_id=line.id,
                    supplier_id=supplier_id, commitment_id=commitment_id, expense_type=expense_type, amount=amount,
                    payment_method=payment_method, payment_order_no=payment_order_no, cheque_no=cheque_no,
                    description=description.strip(), notes=notes, created_by=principal.user_id)
    _validate(session, e)
    session.add(e)
    session.flush()
    return e


def update_draft(session: Session, principal: Principal, e: Expenditure, changes: dict,
                 expected_version: int | None) -> Expenditure:
    ensure_editable(e, expected_version)
    if e.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "expenditure_date" in changes:
        year_for_new_document(session, e.fiscal_year_id, changes["expenditure_date"], principal)
    for k in ("expenditure_date", "amount", "payment_method", "description", "document_no", "supplier_id",
              "commitment_id", "expense_type", "payment_order_no", "cheque_no", "notes"):
        if k in changes:
            setattr(e, k, changes[k])
    _validate(session, e)
    e.row_version += 1
    session.flush()
    return e


def build_posting(session: Session, e: Expenditure, posted_by: uuid.UUID, approved_by: uuid.UUID | None) -> PostingRequest:
    specs = [EntrySpec(e.budget_line_id, "ACTUAL_EXPENDITURE", "ACTUAL", 1, e.amount, description=e.description,
                       commitment_id=e.commitment_id)]
    if e.commitment_id:
        c = commitments.get(session, e.commitment_id)
        liquidation = min(e.amount, commitments.outstanding(session, c))
        if liquidation > 0:
            specs.append(EntrySpec(e.budget_line_id, "COMMITMENT_LIQUIDATION", "COMMITMENT", -1, liquidation,
                                   commitment_id=c.id, description=f"تسييل الارتباط {c.commitment_no}"))
    return PostingRequest(fiscal_year_id=e.fiscal_year_id, entry_date=e.expenditure_date, source_type=SOURCE_TYPE,
                          source_id=e.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=e.document_no, date_is_estimated=e.date_is_estimated)


def post(session: Session, e: Expenditure, actor: uuid.UUID, override_grant_id: uuid.UUID | None = None,
         *, historical_exception: bool = False) -> engine.PostingResult:
    if e.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("المصروف مرحّل أو في حالة نهائية.", code="INVALID_STATE")
    _validate(session, e)
    req = build_posting(session, e, actor, actor)
    req.override_grant_id = override_grant_id
    req.historical_exception = historical_exception
    result = engine.post(session, req)
    e.status, e.posted_by, e.posted_at = "POSTED", actor, datetime.now(UTC)
    e.row_version += 1
    session.flush()
    if e.commitment_id:
        commitments.refresh_status(session, commitments.get(session, e.commitment_id))
    return result
