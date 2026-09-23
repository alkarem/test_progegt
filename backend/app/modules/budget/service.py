"""الاعتماد الأصلي والتعزيز والتخفيض (FR-BG)."""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.budget.models import BudgetDocument, BudgetDocumentLine
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.documents.numbering import next_doc_no
from app.modules.fiscal.models import FiscalYear
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest

SOURCE_TYPE = "budget_document"


def component_for(fy: FiscalYear, kind: str) -> str:
    """الاعتماد الأصلي يذهب دائمًا لمكوّن الاعتماد. التعزيز والتخفيض يذهبان لأساس الرقابة:
    في نمط «التفويض» يعدّلان المفوَّض، وفي النمطين الآخرين يعدّلان الاعتماد (03-financial-model §4)."""
    if kind == "ORIGINAL_BUDGET" or fy.control_basis != "AUTHORIZATION":
        return "APPROPRIATION"
    return "ALLOCATION"


def get(session: Session, doc_id: uuid.UUID) -> BudgetDocument:
    d = session.get(BudgetDocument, doc_id)
    if d is None:
        raise NotFound("مستند الميزانية غير موجود.")
    return d


def lines_of(session: Session, doc_id: uuid.UUID) -> list[BudgetDocumentLine]:
    return list(session.scalars(select(BudgetDocumentLine).where(BudgetDocumentLine.document_id == doc_id)))


def _set_lines(session: Session, principal: Principal, doc: BudgetDocument, lines: list[dict]) -> None:
    if not lines:
        raise ValidationFailed("المستند يجب أن يحتوي سطرًا واحدًا على الأقل.", code="NO_LINES")
    seen = set()
    session.execute(delete(BudgetDocumentLine).where(BudgetDocumentLine.document_id == doc.id))
    for ln in lines:
        bl = engine.get_or_create_line(session, doc.fiscal_year_id, ln["entity_id"], ln["item_id"])
        require_line_scope(session, principal, bl)
        if bl.id in seen:
            raise ValidationFailed("البند مكرر في المستند.", code="DUPLICATE_LINE")
        seen.add(bl.id)
        session.add(BudgetDocumentLine(document_id=doc.id, budget_line_id=bl.id, amount=ln["amount"]))
    session.flush()


def create_draft(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, kind: str, doc_date: date,
                 description: str, reference: str | None, lines: list[dict], doc_no: str | None = None) -> BudgetDocument:
    fy = year_for_new_document(session, fiscal_year_id, doc_date, principal)
    doc = BudgetDocument(id=uuid.uuid4(), fiscal_year_id=fy.id, kind=kind,
                         doc_no=doc_no or next_doc_no(session, fy.id, fy.year, kind), doc_date=doc_date,
                         description=description.strip(), reference=reference, created_by=principal.user_id)
    if session.scalar(select(BudgetDocument.id).where(BudgetDocument.fiscal_year_id == fy.id,
                                                      BudgetDocument.kind == kind, BudgetDocument.doc_no == doc.doc_no)):
        raise Conflict("رقم المستند مستخدم مسبقًا لهذا النوع في السنة.", code="DUPLICATE_DOC_NO")
    session.add(doc)
    session.flush()
    _set_lines(session, principal, doc, lines)
    return doc


def update_draft(session: Session, principal: Principal, doc: BudgetDocument, changes: dict,
                 expected_version: int | None) -> BudgetDocument:
    ensure_editable(doc, expected_version)
    if doc.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "doc_date" in changes:
        year_for_new_document(session, doc.fiscal_year_id, changes["doc_date"], principal)
        doc.doc_date = changes["doc_date"]
    for k in ("description", "reference"):
        if k in changes:
            setattr(doc, k, changes[k])
    if "lines" in changes:
        _set_lines(session, principal, doc, changes["lines"])
    doc.row_version += 1
    session.flush()
    return doc


def total(session: Session, doc: BudgetDocument) -> Decimal:
    return sum((ln.amount for ln in lines_of(session, doc.id)), Decimal("0"))


def build_posting(session: Session, doc: BudgetDocument, posted_by: uuid.UUID,
                  approved_by: uuid.UUID | None) -> PostingRequest:
    fy = session.get(FiscalYear, doc.fiscal_year_id)
    component = component_for(fy, doc.kind)
    direction = -1 if doc.kind == "BUDGET_DECREASE" else 1
    lines = lines_of(session, doc.id)
    if doc.kind == "ORIGINAL_BUDGET":
        # INV: اعتماد أصلي واحد لكل سطر ميزانية
        others = session.scalars(
            select(BudgetDocumentLine.budget_line_id).join(BudgetDocument, BudgetDocument.id == BudgetDocumentLine.document_id)
            .where(BudgetDocument.kind == "ORIGINAL_BUDGET", BudgetDocument.status == "POSTED",
                   BudgetDocument.id != doc.id, BudgetDocumentLine.budget_line_id.in_([ln.budget_line_id for ln in lines]))
        ).all()
        if others:
            raise Conflict("يوجد اعتماد أصلي مرحّل مسبقًا لأحد البنود؛ استخدم التعزيز أو التخفيض.",
                           code="ORIGINAL_ALREADY_POSTED")
    specs = [EntrySpec(budget_line_id=ln.budget_line_id, txn_type=doc.kind, component=component,
                       direction=direction, amount=ln.amount, source_line_id=ln.id, description=doc.description)
             for ln in lines]
    return PostingRequest(fiscal_year_id=doc.fiscal_year_id, entry_date=doc.doc_date, source_type=SOURCE_TYPE,
                          source_id=doc.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=doc.doc_no)


def post(session: Session, doc: BudgetDocument, posted_by: uuid.UUID, approved_by: uuid.UUID | None = None,
         override_grant_id: uuid.UUID | None = None) -> engine.PostingResult:
    if doc.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("المستند مرحّل أو في حالة نهائية.", code="INVALID_STATE")
    req = build_posting(session, doc, posted_by, approved_by)
    req.override_grant_id = override_grant_id
    result = engine.post(session, req)
    doc.status = "POSTED"
    doc.posted_by = posted_by
    doc.posted_at = datetime.now(UTC)
    doc.row_version += 1
    session.flush()
    return result
