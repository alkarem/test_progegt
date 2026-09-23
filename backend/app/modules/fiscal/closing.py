"""الإقفال السنوي وترحيل الارتباطات (FR-YE، 03-financial-model §9، D-08).

1. لا مستندات معلقة في دورة الموافقة (وإلا يُرفض الإقفال مع قائمتها).
2. تحرير كل حجز مبدئي قائم (CLOSING على RESERVATION).
3. لكل ارتباط قائم: CLOSING في السنة القديمة (فترة التسويات 13)، وارتباط جديد في السنة التالية على
   بند الترحيل (2/28 افتراضيًا) بقيد CARRY_FORWARD مرتبط بالأصل.
4. إقفال كل الفترات، وحالة السنة CLOSED. كل ذلك في معاملة واحدة.
"""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.modules.commitments import service as cm
from app.modules.commitments.models import Commitment
from app.modules.documents.numbering import next_doc_no
from app.modules.fiscal.models import FiscalPeriod, FiscalYear
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest
from app.modules.ledger.models import BudgetLine

DOC_TABLES = ("budget_documents", "authorizations", "transfers", "commitments", "expenditures", "adjustments")


def _pending(session: Session, fy: FiscalYear) -> list[dict]:
    out = []
    for t in DOC_TABLES:
        n = session.scalar(text(f"SELECT count(*) FROM {t} WHERE fiscal_year_id = :f"  # noqa: S608 (أسماء ثابتة)
                                " AND status IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW', 'RETURNED')"), {"f": fy.id})
        if n:
            out.append({"table": t, "count": n})
    return out


def _next_year(session: Session, fy: FiscalYear) -> FiscalYear | None:
    return session.scalar(select(FiscalYear).where(FiscalYear.year == fy.year + 1))


def _carry_needs(session: Session, fy: FiscalYear, nxt: FiscalYear | None, commitments: list[Commitment]) -> list[dict]:
    """ما يحتاجه بند الترحيل في السنة التالية من رصيد متاح لاستيعاب الارتباطات المرحّلة."""
    if nxt is None:
        return []
    from app.modules.catalog.models import BudgetItem
    need: dict[tuple, Decimal] = {}
    for c in commitments:
        old = session.get(BudgetLine, c.budget_line_id)
        key = (old.entity_id, nxt.carry_forward_item_id or old.item_id)
        need[key] = need.get(key, Decimal("0")) + cm.outstanding(session, c)
    out = []
    for (entity_id, item_id), amount in need.items():
        line = engine.find_line(session, nxt.id, entity_id, item_id)
        available = engine.current_position(session, line).available if line else Decimal("0")
        item = session.get(BudgetItem, item_id)
        out.append({"item_code": item.code, "item_name": item.name, "required": str(amount),
                    "available": str(available), "shortfall": str(max(amount - available, Decimal("0")))})
    return out


def preview(session: Session, fy: FiscalYear) -> dict:
    open_items = [c for c in session.scalars(select(Commitment).where(
        Commitment.fiscal_year_id == fy.id, Commitment.status == "POSTED")) if cm.outstanding(session, c) > 0]
    nxt = _next_year(session, fy)
    return {
        "fiscal_year": fy.year, "status": fy.status, "pending_documents": _pending(session, fy),
        "reservations_to_release": [{"id": str(c.id), "no": c.commitment_no, "amount": str(cm.outstanding(session, c))}
                                    for c in open_items if c.commitment_type == cm.PR],
        "commitments_to_carry": [{"id": str(c.id), "no": c.commitment_no, "amount": str(cm.outstanding(session, c))}
                                 for c in open_items if c.commitment_type != cm.PR],
        "next_year": nxt.year if nxt else None, "next_year_status": nxt.status if nxt else None,
        "carry_forward_funding": _carry_needs(session, fy, nxt, [c for c in open_items if c.commitment_type != cm.PR]),
    }


def close_year(session: Session, fy: FiscalYear, actor: uuid.UUID) -> dict:
    if fy.status not in ("OPEN", "CLOSING"):
        raise Conflict("السنة ليست مفتوحة.", code="INVALID_STATE")
    pending = _pending(session, fy)
    if pending:
        raise Conflict("توجد مستندات غير منتهية يجب ترحيلها أو إلغاؤها قبل الإقفال.", code="PENDING_DOCUMENTS",
                       details={"pending": pending})
    session.execute(update(FiscalPeriod).where(FiscalPeriod.fiscal_year_id == fy.id, FiscalPeriod.period_no == 13)
                    .values(status="OPEN"))
    fy.status = "CLOSING"
    session.flush()
    nxt = _next_year(session, fy)
    to_carry = [c for c in session.scalars(select(Commitment).where(
        Commitment.fiscal_year_id == fy.id, Commitment.status == "POSTED", Commitment.commitment_type != cm.PR))
        if cm.outstanding(session, c) > 0]
    if to_carry and (nxt is None or nxt.status != "OPEN"):
        raise Conflict(f"توجد ارتباطات قائمة ويجب فتح السنة {fy.year + 1} أولًا لترحيلها (D-08).",
                       code="NEXT_YEAR_REQUIRED")
    unfunded = [x for x in _carry_needs(session, fy, nxt, to_carry) if Decimal(x["shortfall"]) > 0]
    if unfunded:
        raise Conflict(f"بند الترحيل في السنة {fy.year + 1} لا يملك رصيدًا كافيًا لاستيعاب الارتباطات المرحّلة؛"
                       " اعتمد له المبلغ المطلوب أولًا.", code="CARRY_FORWARD_UNFUNDED", details={"items": unfunded})
    released = carried = Decimal("0")
    carried_docs = []
    for c in session.scalars(select(Commitment).where(Commitment.fiscal_year_id == fy.id,
                                                      Commitment.status == "POSTED").order_by(Commitment.commitment_date)):
        out = cm.outstanding(session, c)
        if out <= 0:
            continue
        engine.post(session, PostingRequest(
            fiscal_year_id=fy.id, entry_date=fy.end_date, source_type="fiscal_year_closing", source_id=fy.id,
            posted_by=actor, approved_by=actor, allow_adjustment_period=True, document_no=f"CLOSE-{fy.year}",
            entries=[EntrySpec(c.budget_line_id, "CLOSING", cm.component_of(c), -1, out, commitment_id=c.id,
                               description=f"إقفال السنة {fy.year}: {c.commitment_no}")]))
        if c.commitment_type == cm.PR:
            released += out
        else:
            carried += out
            carried_docs.append(_carry_forward(session, c, out, fy, nxt, actor))
        cm.refresh_status(session, c)
        c.commitment_status = "CLOSED"
    session.execute(update(FiscalPeriod).where(FiscalPeriod.fiscal_year_id == fy.id).values(
        status="CLOSED", closed_by=actor, closed_at=datetime.now(UTC)))
    fy.status = "CLOSED"
    session.flush()
    return {"fiscal_year": fy.year, "reservations_released": str(released), "commitments_carried": str(carried),
            "carried_documents": carried_docs}


def _carry_forward(session: Session, c: Commitment, amount: Decimal, fy: FiscalYear, nxt: FiscalYear,
                   actor: uuid.UUID) -> str:
    old_line = session.get(BudgetLine, c.budget_line_id)
    item_id = nxt.carry_forward_item_id or old_line.item_id
    line = engine.get_or_create_line(session, nxt.id, old_line.entity_id, item_id)
    new = Commitment(id=uuid.uuid4(), fiscal_year_id=nxt.id, commitment_type=c.commitment_type,
                     commitment_no=next_doc_no(session, nxt.id, nxt.year, c.commitment_type),
                     commitment_date=nxt.start_date, budget_line_id=line.id, supplier_id=c.supplier_id,
                     amount=amount, description=f"مرحّل من {fy.year}: {c.description}", reference=c.commitment_no,
                     carried_from_id=c.id, created_by=actor, status="DRAFT")
    session.add(new)
    session.flush()
    engine.post(session, PostingRequest(
        fiscal_year_id=nxt.id, entry_date=nxt.start_date, source_type="commitment", source_id=new.id, posted_by=actor,
        approved_by=actor, document_no=new.commitment_no,
        entries=[EntrySpec(line.id, "CARRY_FORWARD", "COMMITMENT", 1, amount, commitment_id=new.id,
                           description=f"ترحيل الارتباط {c.commitment_no} من {fy.year}")]))
    new.status, new.posted_by, new.posted_at, new.commitment_status = "POSTED", actor, datetime.now(UTC), "APPROVED"
    session.flush()
    return new.commitment_no
