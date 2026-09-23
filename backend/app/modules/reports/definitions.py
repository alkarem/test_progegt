"""التقارير العشرون (09-reports §2). كل رقم محسوب من القيود أو الأرصدة المشتقة منها."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import ValidationFailed
from app.modules.authorizations import service as auth_service
from app.modules.authorizations.models import Authorization
from app.modules.catalog.models import BudgetItem, Supplier
from app.modules.commitments import service as cm
from app.modules.commitments.models import Commitment
from app.modules.expenditures.models import Expenditure
from app.modules.fiscal.models import FiscalYear
from app.modules.ledger.models import BudgetLine, LedgerEntry
from app.modules.ledger.router import level_of
from app.modules.reports.engine import (
    ZERO,
    Column,
    ReportData,
    ReportDef,
    pdate,
    positions,
    register,
    require_year,
)
from app.modules.users.models import User

M = "money"
LEVELS = {"NORMAL": "طبيعي", "WARNING": "تحذير", "CRITICAL": "حرج", "EXHAUSTED": "مستنفد", "EXCEEDED": "متجاوز",
          "NO_BUDGET": "بلا اعتماد"}
STATUS = {"DRAFT": "مسودة", "SUBMITTED": "مقدم", "IN_REVIEW": "قيد المراجعة", "POSTED": "مرحّل",
          "REJECTED": "مرفوض", "RETURNED": "مُرجع", "CANCELLED": "ملغى", "REVERSED": "معكوس"}
TXN = {"ORIGINAL_BUDGET": "اعتماد أصلي", "BUDGET_ALLOCATION": "توزيع تفويض", "BUDGET_INCREASE": "تعزيز",
       "BUDGET_DECREASE": "تخفيض", "TRANSFER_IN": "مناقلة واردة", "TRANSFER_OUT": "مناقلة صادرة",
       "PRE_COMMITMENT": "حجز مبدئي", "RESERVATION_RELEASE": "تحرير حجز", "COMMITMENT": "ارتباط",
       "COMMITMENT_LIQUIDATION": "تسييل ارتباط", "ACTUAL_EXPENDITURE": "مصروف فعلي", "ADJUSTMENT": "تسوية",
       "REVERSAL": "قيد عكسي", "CANCELLATION": "إلغاء", "CLOSING": "إقفال", "CARRY_FORWARD": "ترحيل لسنة تالية"}


def _item(it: BudgetItem) -> dict:
    return {"item_code": it.code, "item_name": it.name}


ITEM_COLS = [Column("item_code", "البند"), Column("item_name", "اسم البند")]


# 1 ---------------------------------------------------------------------------
def rpt_position(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    as_of = pdate(params, "as_of")
    rows = []
    for _, it, pos in positions(session, p, fy, params, as_of=as_of):
        rows.append(_item(it) | {"appropriation": pos.appropriation, "allocation": pos.allocation,
                                 "transfer_in": pos.transfer_in, "transfer_out": pos.transfer_out,
                                 "actual": pos.actual, "commitment": pos.commitment, "reservation": pos.counted_reservation,
                                 "book_balance": pos.book_balance, "available": pos.available,
                                 "utilization_rate": pos.utilization_rate})
    return ReportData("RPT-01", "تقرير موقف الاعتماد", ITEM_COLS + [
        Column("appropriation", "الاعتماد", M, True), Column("allocation", "المفوَّض", M, True),
        Column("transfer_in", "وارد", M, True), Column("transfer_out", "صادر", M, True),
        Column("actual", "الفعلي", M, True), Column("commitment", "الارتباطات", M, True),
        Column("reservation", "الحجوزات", M, True), Column("book_balance", "الرصيد الدفتري", M, True),
        Column("available", "المتاح", M, True), Column("utilization_rate", "الاستخدام %", "percent")],
        rows, params, subtitle=f"السنة المالية {fy.year}" + (f" — الموقف في {as_of}" if as_of else ""))


# 2 ---------------------------------------------------------------------------
def rpt_central(session: Session, p: Principal, params: dict) -> ReportData:
    """التقرير المركزي: البند | الاعتماد | المناقلات | الارتباطات | المصروف | الرصيد | نسبة التنفيذ (09-reports §3)."""
    fy = require_year(session, p, params)
    rows = []
    for ln, it, pos in positions(session, p, fy, params, as_of=pdate(params, "as_of")):
        rows.append(_item(it) | {"budget_line_id": str(ln.id), "budget": pos.control_base,
                                 "transfers": pos.transfer_in - pos.transfer_out,
                                 "commitments": pos.commitment + pos.counted_reservation, "actual": pos.actual,
                                 "available": pos.available, "execution_rate": pos.actual_rate})
    return ReportData("RPT-02", "تقرير تنفيذ الباب الثاني", ITEM_COLS + [
        Column("budget", "الاعتماد", M, True), Column("transfers", "المناقلات", M, True),
        Column("commitments", "الارتباطات", M, True), Column("actual", "المصروف", M, True),
        Column("available", "الرصيد", M, True), Column("execution_rate", "نسبة التنفيذ %", "percent")],
        rows, params, subtitle=f"سجل مراقبة الاعتماد — السنة المالية {fy.year}",
        notes=["الاعتماد = أساس الرقابة (المفوَّض أو الاعتماد حسب إعداد السنة). نسبة التنفيذ = الفعلي ÷ (الاعتماد + المناقلات)."])


# 3 ---------------------------------------------------------------------------
def rpt_expenditures(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    stmt = select(Expenditure, BudgetItem, Supplier).join(BudgetLine, BudgetLine.id == Expenditure.budget_line_id).join(
        BudgetItem, BudgetItem.id == BudgetLine.item_id).outerjoin(Supplier, Supplier.id == Expenditure.supplier_id).where(
        Expenditure.fiscal_year_id == fy.id, Expenditure.status == params.get("status", "POSTED"))
    if pdate(params, "date_from"):
        stmt = stmt.where(Expenditure.expenditure_date >= pdate(params, "date_from"))
    if pdate(params, "date_to"):
        stmt = stmt.where(Expenditure.expenditure_date <= pdate(params, "date_to"))
    if params.get("supplier_id"):
        stmt = stmt.where(Expenditure.supplier_id == uuid.UUID(str(params["supplier_id"])))
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    rows = [{"date": e.expenditure_date, "document_no": e.document_no, "item_code": it.code,
             "beneficiary": s.name if s else "", "description": e.description, "payment_method": e.payment_method,
             "payment_order_no": e.payment_order_no or "", "cheque_no": e.cheque_no or "", "amount": e.amount,
             "estimated": "تقديري" if e.date_is_estimated else ""}
            for e, it, s in session.execute(stmt.order_by(Expenditure.expenditure_date, Expenditure.document_no))]
    return ReportData("RPT-03", "تقرير المصروفات", [
        Column("date", "التاريخ", "date"), Column("document_no", "رقم المستند"), Column("item_code", "البند"),
        Column("beneficiary", "المستفيد"), Column("description", "البيان"), Column("payment_method", "طريقة الدفع"),
        Column("payment_order_no", "أمر الصرف"), Column("cheque_no", "الشيك"), Column("amount", "المبلغ", M, True),
        Column("estimated", "التاريخ")], rows, params, subtitle=f"السنة المالية {fy.year}")


# 4 ---------------------------------------------------------------------------
def rpt_commitments(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    stmt = select(Commitment, BudgetItem, Supplier).join(BudgetLine, BudgetLine.id == Commitment.budget_line_id).join(
        BudgetItem, BudgetItem.id == BudgetLine.item_id).outerjoin(Supplier, Supplier.id == Commitment.supplier_id).where(
        Commitment.fiscal_year_id == fy.id, Commitment.status.in_(("POSTED", "REVERSED")))
    if params.get("open_only"):
        stmt = stmt.where(Commitment.commitment_status.in_(("APPROVED", "PARTIALLY_PAID")))
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    today = date.today()
    rows = [{"commitment_no": c.commitment_no, "type": c.commitment_type, "date": c.commitment_date,
             "item_code": it.code, "supplier": s.name if s else "", "amount": c.amount, "paid": cm.paid(session, c),
             "cancelled": cm.cancelled(session, c), "outstanding": cm.outstanding(session, c),
             "status": c.commitment_status, "age_days": (today - c.commitment_date).days}
            for c, it, s in session.execute(stmt.order_by(Commitment.commitment_date))]
    return ReportData("RPT-04", "تقرير الارتباطات", [
        Column("commitment_no", "الرقم"), Column("type", "النوع"), Column("date", "التاريخ", "date"),
        Column("item_code", "البند"), Column("supplier", "المورد"), Column("amount", "القيمة", M, True),
        Column("paid", "المسدد", M, True), Column("cancelled", "الملغى", M, True),
        Column("outstanding", "القائم", M, True), Column("status", "الحالة"), Column("age_days", "العمر (يوم)", "int")],
        rows, params, subtitle=f"السنة المالية {fy.year}")


# 5 ---------------------------------------------------------------------------
def rpt_transfers(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [dict(r._mapping) for r in session.execute(text("""
        SELECT t.transfer_no, t.transfer_date AS date, fi.code AS from_code, ti.code AS to_code, tl.amount,
               t.reason, coalesce(t.approval_no, '') AS approval_no, coalesce(u.full_name, '') AS approved_by,
               t.status
        FROM transfer_lines tl JOIN transfers t ON t.id = tl.transfer_id
        JOIN budget_lines fl ON fl.id = tl.from_line_id JOIN budget_items fi ON fi.id = fl.item_id
        JOIN budget_lines tl2 ON tl2.id = tl.to_line_id JOIN budget_items ti ON ti.id = tl2.item_id
        LEFT JOIN users u ON u.id = t.approved_by
        WHERE t.fiscal_year_id = :f AND t.status IN ('POSTED', 'REVERSED') ORDER BY t.transfer_date, t.transfer_no"""),
        {"f": fy.id})]
    for r in rows:
        r["status"] = STATUS.get(r["status"], r["status"])
    return ReportData("RPT-05", "تقرير المناقلات", [
        Column("transfer_no", "الرقم"), Column("date", "التاريخ", "date"), Column("from_code", "من بند"),
        Column("to_code", "إلى بند"), Column("amount", "المبلغ", M, True), Column("reason", "السبب"),
        Column("approval_no", "رقم القرار"), Column("approved_by", "المعتمد"), Column("status", "الحالة")],
        rows, params, subtitle=f"السنة المالية {fy.year}")


# 6 ---------------------------------------------------------------------------
def rpt_authorizations(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = []
    for a in session.scalars(select(Authorization).where(Authorization.fiscal_year_id == fy.id,
                                                         Authorization.status.in_(("POSTED", "REVERSED")))
                             .order_by(Authorization.auth_date)):
        allocated = auth_service.allocated_total(session, a)
        rows.append({"auth_no": a.auth_no, "type": {"FINANCIAL": "مالي", "DEPARTMENTAL": "مصلحي"}.get(a.auth_type, "أخرى"),
                     "date": a.auth_date, "period": f"{a.period_from or ''} — {a.period_to or ''}".strip(" —"),
                     "amount": a.amount, "allocated": allocated, "unallocated": a.amount - allocated,
                     "purpose": a.purpose, "status": STATUS.get(a.status, a.status)})
    return ReportData("RPT-06", "تقرير التفويضات", [
        Column("auth_no", "رقم التفويض"), Column("type", "النوع"), Column("date", "التاريخ", "date"),
        Column("period", "الفترة"), Column("amount", "القيمة", M, True), Column("allocated", "الموزع", M, True),
        Column("unallocated", "غير الموزع", M, True), Column("purpose", "الغرض"), Column("status", "الحالة")],
        rows, params, subtitle=f"السنة المالية {fy.year}")


# 7 ---------------------------------------------------------------------------
def rpt_available(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [_item(it) | {"budget": pos.adjusted_budget, "used": pos.actual + pos.commitment + pos.counted_reservation,
                         "available": pos.available, "utilization_rate": pos.utilization_rate,
                         "level": LEVELS[level_of(pos)]}
            for _, it, pos in positions(session, p, fy, params)]
    rows.sort(key=lambda r: r["available"])
    return ReportData("RPT-07", "تقرير الرصيد المتاح", ITEM_COLS + [
        Column("budget", "الاعتماد بعد المناقلات", M, True), Column("used", "المستخدم", M, True),
        Column("available", "المتاح", M, True), Column("utilization_rate", "الاستخدام %", "percent"),
        Column("level", "المستوى")], rows, params, subtitle=f"السنة المالية {fy.year}")


# 8 ---------------------------------------------------------------------------
def rpt_overruns(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = []
    for ln, it, pos in positions(session, p, fy, params):
        if pos.available >= 0:
            continue
        hist = session.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.budget_line_id == ln.id, LedgerEntry.is_historical_exception.is_(True)))
        overr = session.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.budget_line_id == ln.id, LedgerEntry.override_grant_id.is_not(None)))
        rows.append(_item(it) | {"budget": pos.adjusted_budget, "actual": pos.actual, "commitment": pos.commitment,
                                 "excess": -pos.available,
                                 "cause": "استثناء تاريخي (مستورد)" if hist else ("منحة استثناء" if overr else "—")})
    return ReportData("RPT-08", "تقرير التجاوزات", ITEM_COLS + [
        Column("budget", "الاعتماد بعد المناقلات", M, True), Column("actual", "الفعلي", M, True),
        Column("commitment", "الارتباطات", M, True), Column("excess", "قيمة التجاوز", M, True),
        Column("cause", "المصدر")], rows, params, subtitle=f"السنة المالية {fy.year}")


# 9 و10 --------------------------------------------------------------------------
_DOC_UNION = """
  SELECT 'مستند ميزانية' AS type, id, doc_no, doc_date AS date, status, created_by, updated_at, fiscal_year_id,
         (SELECT sum(amount) FROM budget_document_lines WHERE document_id = budget_documents.id) AS amount
    FROM budget_documents
  UNION ALL SELECT 'تفويض', id, auth_no, auth_date, status, created_by, updated_at, fiscal_year_id, amount FROM authorizations
  UNION ALL SELECT 'مناقلة', id, transfer_no, transfer_date, status, created_by, updated_at, fiscal_year_id,
         (SELECT sum(amount) FROM transfer_lines WHERE transfer_id = transfers.id) FROM transfers
  UNION ALL SELECT 'ارتباط', id, commitment_no, commitment_date, status, created_by, updated_at, fiscal_year_id, amount
    FROM commitments
  UNION ALL SELECT 'مصروف', id, document_no, expenditure_date, status, created_by, updated_at, fiscal_year_id, amount
    FROM expenditures
  UNION ALL SELECT 'تسوية', id, adjustment_no, adjustment_date, status, created_by, updated_at, fiscal_year_id,
         cancel_amount FROM adjustments
"""


def rpt_pending(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [dict(r._mapping) for r in session.execute(text("""
        SELECT d.type, d.doc_no, d.date, d.amount, coalesce(ws.name, '') AS step, u.full_name AS created_by,
               (current_date - wi.step_entered_at::date) AS days_in_step
        FROM (DOC_UNION) d JOIN users u ON u.id = d.created_by
        LEFT JOIN workflow_instances wi ON wi.source_id = d.id
        LEFT JOIN workflow_steps ws ON ws.id = wi.current_step_id
        WHERE d.fiscal_year_id = :f AND d.status IN ('SUBMITTED', 'IN_REVIEW')
        ORDER BY days_in_step DESC NULLS LAST""".replace("DOC_UNION", _DOC_UNION)), {"f": fy.id})]
    return ReportData("RPT-09", "تقرير العمليات المعلقة", [
        Column("type", "النوع"), Column("doc_no", "الرقم"), Column("date", "التاريخ", "date"),
        Column("amount", "المبلغ", M, True), Column("step", "المرحلة الحالية"), Column("created_by", "المنشئ"),
        Column("days_in_step", "أيام في المرحلة", "int")], rows, params, subtitle=f"السنة المالية {fy.year}")


def rpt_cancelled(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [dict(r._mapping) for r in session.execute(text("""
        SELECT d.type, d.doc_no, d.date, d.amount, d.status, u.full_name AS created_by, d.updated_at::date AS changed_on,
               coalesce((SELECT comment FROM workflow_actions wa JOIN workflow_instances wi ON wi.id = wa.instance_id
                         WHERE wi.source_id = d.id AND wa.action IN ('CANCEL', 'REJECT') ORDER BY wa.id DESC LIMIT 1),
                        (SELECT reason FROM adjustments a WHERE a.reverses_source_id = d.id AND a.status = 'POSTED'
                         LIMIT 1), '') AS reason
        FROM (DOC_UNION) d JOIN users u ON u.id = d.created_by
        WHERE d.fiscal_year_id = :f AND d.status IN ('CANCELLED', 'REJECTED', 'REVERSED') ORDER BY d.date"""
        .replace("DOC_UNION", _DOC_UNION)), {"f": fy.id})]
    for r in rows:
        r["status"] = STATUS.get(r["status"], r["status"])
    return ReportData("RPT-10", "تقرير العمليات الملغاة والمعكوسة", [
        Column("type", "النوع"), Column("doc_no", "الرقم"), Column("date", "التاريخ", "date"),
        Column("amount", "المبلغ", M, True), Column("status", "الحالة"), Column("reason", "السبب"),
        Column("created_by", "المنشئ"), Column("changed_on", "تاريخ التغيير", "date")], rows, params,
        subtitle=f"السنة المالية {fy.year}")


# 11 و17 ---------------------------------------------------------------------------
def _entries(session: Session, p: Principal, fy: FiscalYear, params: dict, line_id: uuid.UUID | None = None):
    stmt = select(LedgerEntry, BudgetItem, User).join(BudgetLine, BudgetLine.id == LedgerEntry.budget_line_id).join(
        BudgetItem, BudgetItem.id == BudgetLine.item_id).join(User, User.id == LedgerEntry.posted_by).where(
        LedgerEntry.fiscal_year_id == fy.id)
    if line_id:
        stmt = stmt.where(LedgerEntry.budget_line_id == line_id)
    if params.get("txn_type"):
        stmt = stmt.where(LedgerEntry.txn_type == params["txn_type"])
    if pdate(params, "date_from"):
        stmt = stmt.where(LedgerEntry.entry_date >= pdate(params, "date_from"))
    if pdate(params, "date_to"):
        stmt = stmt.where(LedgerEntry.entry_date <= pdate(params, "date_to"))
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return session.execute(stmt.order_by(LedgerEntry.entry_date, LedgerEntry.entry_no))


def rpt_item_movement(session: Session, p: Principal, params: dict) -> ReportData:
    """كشف حساب البند: كل القيود بالتسلسل مع الرصيد المتاح المتحرك."""
    fy = require_year(session, p, params)
    if not params.get("budget_line_id"):
        raise ValidationFailed("حدد البند (سطر الميزانية).", code="LINE_REQUIRED")
    line_id = uuid.UUID(str(params["budget_line_id"]))
    line = session.get(BudgetLine, line_id)
    if line is None or line.fiscal_year_id != fy.id:
        raise ValidationFailed("سطر الميزانية لا ينتمي للسنة.", code="LINE_YEAR_MISMATCH")
    p.require_scope("ITEM", line.item_id)
    from app.modules.ledger.engine import Position
    pos = Position(fy.control_basis, fy.count_reservations)
    rows, item = [], session.get(BudgetItem, line.item_id)
    for e, _, u in _entries(session, p, fy, {}, line_id):
        pos = pos.with_deltas({e.component: e.direction * e.amount})
        rows.append({"entry_no": e.entry_no, "date": e.entry_date, "txn": TXN[e.txn_type], "document_no": e.document_no or "",
                     "description": e.description or "", "amount": e.direction * e.amount, "available_after": pos.available,
                     "posted_by": u.full_name})
    return ReportData("RPT-11", "تقرير حركة بند", [
        Column("entry_no", "القيد", "int"), Column("date", "التاريخ", "date"), Column("txn", "الحركة"),
        Column("document_no", "المستند"), Column("description", "البيان"), Column("amount", "المبلغ", M),
        Column("available_after", "المتاح بعد القيد", M), Column("posted_by", "رحّله")], rows, params,
        subtitle=f"{item.code} {item.name} — السنة المالية {fy.year}")


def rpt_ledger_detail(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [{"entry_no": e.entry_no, "date": e.entry_date, "item_code": it.code, "txn": TXN[e.txn_type],
             "component": e.component, "amount": e.direction * e.amount, "source": e.source_type,
             "document_no": e.document_no or "", "posted_by": u.full_name, "posted_at": e.posted_at,
             "flags": " ".join(x for x in ("استثناء تاريخي" if e.is_historical_exception else "",
                                           "تاريخ تقديري" if e.date_is_estimated else "",
                                           "منحة استثناء" if e.override_grant_id else "") if x)}
            for e, it, u in _entries(session, p, fy, params)]
    return ReportData("RPT-17", "كشف تفصيلي للحركة", [
        Column("entry_no", "القيد", "int"), Column("date", "التاريخ", "date"), Column("item_code", "البند"),
        Column("txn", "الحركة"), Column("component", "المكوّن"), Column("amount", "المبلغ", M, True),
        Column("source", "المصدر"), Column("document_no", "المستند"), Column("posted_by", "رحّله"),
        Column("posted_at", "وقت الترحيل", "date"), Column("flags", "علامات")], rows, params,
        subtitle=f"السنة المالية {fy.year}")


# 12 ---------------------------------------------------------------------------
def rpt_fiscal_year(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    pos = [x[2] for x in positions(session, p, fy, params)]
    s = lambda f: sum((f(x) for x in pos), ZERO)  # noqa: E731
    rows = [
        {"metric": "الاعتماد الأصلي والتعديلات", "value": s(lambda x: x.appropriation)},
        {"metric": "المفوَّض (التفويضات الموزعة)", "value": s(lambda x: x.allocation)},
        {"metric": "المناقلات الواردة", "value": s(lambda x: x.transfer_in)},
        {"metric": "المناقلات الصادرة", "value": s(lambda x: x.transfer_out)},
        {"metric": "الاعتماد بعد المناقلات", "value": s(lambda x: x.adjusted_budget)},
        {"metric": "المصروف الفعلي", "value": s(lambda x: x.actual)},
        {"metric": "الارتباطات القائمة", "value": s(lambda x: x.commitment)},
        {"metric": "الحجوزات القائمة", "value": s(lambda x: x.counted_reservation)},
        {"metric": "الرصيد الدفتري", "value": s(lambda x: x.book_balance)},
        {"metric": "الرصيد المتاح", "value": s(lambda x: x.available)},
        {"metric": "عدد البنود المتجاوزة", "value": Decimal(sum(1 for x in pos if x.available < 0))},
    ]
    return ReportData("RPT-12", "تقرير السنة المالية", [Column("metric", "المؤشر"), Column("value", "القيمة", M)],
                      rows, params, subtitle=f"السنة المالية {fy.year} — الحالة: {fy.status}")


# 13 و14 و15 ---------------------------------------------------------------------
def _period_report(session: Session, p: Principal, params: dict, code: str, title: str, start: date, end: date,
                   subtitle: str) -> ReportData:
    fy = require_year(session, p, params)
    moves = {ln.id: pos for ln, _, pos in positions(session, p, fy, params, as_of=end, since=start)}
    rows = []
    for ln, it, closing in positions(session, p, fy, params, as_of=end):
        m = moves.get(ln.id)
        rows.append(_item(it) | {"period_budget": (m.control_base + m.transfer_in - m.transfer_out) if m else ZERO,
                                 "period_commitment": m.commitment if m else ZERO,
                                 "period_actual": m.actual if m else ZERO,
                                 "cum_actual": closing.actual, "available": closing.available,
                                 "rate": closing.actual_rate})
    est = session.scalar(select(func.coalesce(func.sum(LedgerEntry.direction * LedgerEntry.amount), 0)).where(
        LedgerEntry.fiscal_year_id == fy.id, LedgerEntry.component == "ACTUAL",
        LedgerEntry.date_is_estimated.is_(True), LedgerEntry.entry_date.between(start, end)))
    notes = [f"يشمل الفترة مصروفات بتاريخ تقديري بقيمة {est:,.3f} (D-04)."] if est else []
    return ReportData(code, title, ITEM_COLS + [
        Column("period_budget", "حركة الاعتماد في الفترة", M, True), Column("period_commitment", "ارتباطات الفترة", M, True),
        Column("period_actual", "مصروف الفترة", M, True), Column("cum_actual", "المصروف التراكمي", M, True),
        Column("available", "المتاح في نهاية الفترة", M, True), Column("rate", "نسبة التنفيذ %", "percent")],
        rows, params, subtitle=subtitle, notes=notes)


def _year_of(session, p, params) -> FiscalYear:
    return require_year(session, p, params)


def rpt_monthly(session: Session, p: Principal, params: dict) -> ReportData:
    fy = _year_of(session, p, params)
    m = int(params.get("month") or 1)
    if not 1 <= m <= 12:
        raise ValidationFailed("الشهر بين 1 و12.", code="INVALID_MONTH")
    start = date(fy.year, m, 1)
    end = (date(fy.year + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    return _period_report(session, p, params, "RPT-13", "التقرير الشهري", start, end, f"شهر {m}/{fy.year}")


def rpt_quarterly(session: Session, p: Principal, params: dict) -> ReportData:
    fy = _year_of(session, p, params)
    q = int(params.get("quarter") or 1)
    if not 1 <= q <= 4:
        raise ValidationFailed("الربع بين 1 و4.", code="INVALID_QUARTER")
    start = date(fy.year, 3 * q - 2, 1)
    end = date(fy.year + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1)
    return _period_report(session, p, params, "RPT-14", "التقرير الربع سنوي", start, end, f"الربع {q} — {fy.year}")


def rpt_annual(session: Session, p: Principal, params: dict) -> ReportData:
    fy = _year_of(session, p, params)
    data = _period_report(session, p, params, "RPT-15", "التقرير السنوي", fy.start_date, fy.end_date,
                          f"السنة المالية {fy.year}")
    prev = session.scalar(select(FiscalYear).where(FiscalYear.year == fy.year - 1))
    if prev:
        prev_actual = {it.code: pos.actual for _, it, pos in positions(session, p, prev, {})}
        for r in data.rows:
            r["prev_actual"] = prev_actual.get(r["item_code"], ZERO)
        data.columns.append(Column("prev_actual", f"مصروف {prev.year}", M, True))
    return data


# 16 ---------------------------------------------------------------------------
def rpt_audit(session: Session, p: Principal, params: dict) -> ReportData:
    p.require("audit.view")
    df, dt = pdate(params, "date_from"), pdate(params, "date_to")
    rows = [dict(r._mapping) for r in session.execute(text("""
        SELECT a.id, a.occurred_at, coalesce(u.full_name, 'النظام') AS user_name, coalesce(a.ip, '') AS ip,
               a.action, coalesce(a.table_name, '') AS table_name, coalesce(a.record_id, '') AS record_id,
               coalesce(array_to_string(a.changed_fields, '، '), '') AS changed, coalesce(a.reason, '') AS reason
        FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
        WHERE (CAST(:df AS date) IS NULL OR a.occurred_at >= CAST(:df AS date))
          AND (CAST(:dt AS date) IS NULL OR a.occurred_at < CAST(:dt AS date) + 1)
          AND (CAST(:tbl AS text) IS NULL OR a.table_name = :tbl)
        ORDER BY a.id DESC LIMIT 5000"""), {"df": df, "dt": dt, "tbl": params.get("table_name")})]
    return ReportData("RPT-16", "سجل التدقيق", [
        Column("id", "#", "int"), Column("occurred_at", "الوقت", "date"), Column("user_name", "المستخدم"),
        Column("ip", "IP"), Column("action", "الإجراء"), Column("table_name", "الجدول"), Column("record_id", "السجل"),
        Column("changed", "الحقول المتغيرة"), Column("reason", "السبب")], rows, params)


# 18 ---------------------------------------------------------------------------
def rpt_budget_vs_actual(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = [_item(it) | {"budget": pos.adjusted_budget, "actual": pos.actual,
                         "difference": pos.adjusted_budget - pos.actual, "rate": pos.actual_rate}
            for _, it, pos in positions(session, p, fy, params)]
    return ReportData("RPT-18", "المقارنة بين الاعتماد والمصروف", ITEM_COLS + [
        Column("budget", "الاعتماد بعد المناقلات", M, True), Column("actual", "المصروف الفعلي", M, True),
        Column("difference", "الفرق", M, True), Column("rate", "نسبة التنفيذ %", "percent")], rows, params,
        subtitle=f"السنة المالية {fy.year}")


# 19 ---------------------------------------------------------------------------
def rpt_final_account(session: Session, p: Principal, params: dict) -> ReportData:
    fy = require_year(session, p, params)
    rows = []
    for ln, it, pos in positions(session, p, fy, params):
        carried = session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.budget_line_id == ln.id, LedgerEntry.txn_type == "CLOSING",
            LedgerEntry.component == "COMMITMENT"))
        rows.append(_item(it) | {"final_budget": pos.adjusted_budget, "actual": pos.actual,
                                 "saving": max(pos.adjusted_budget - pos.actual, ZERO),
                                 "overrun": max(pos.actual - pos.adjusted_budget, ZERO),
                                 "outstanding": pos.commitment, "carried_forward": carried})
    return ReportData("RPT-19", "الحساب الختامي للباب", ITEM_COLS + [
        Column("final_budget", "الاعتماد النهائي", M, True), Column("actual", "الفعلي", M, True),
        Column("saving", "الوفر", M, True), Column("overrun", "التجاوز", M, True),
        Column("outstanding", "ارتباطات قائمة", M, True), Column("carried_forward", "ارتباطات مرحّلة", M, True)],
        rows, params, subtitle=f"السنة المالية {fy.year} — الحالة: {fy.status}",
        notes=[] if fy.status == "CLOSED" else ["السنة غير مقفلة بعد: الأرقام مؤقتة."])


# 20 ---------------------------------------------------------------------------
CUSTOM_GROUPS = {"item": ("bi.code", "البند"), "month": ("to_char(le.entry_date, 'YYYY-MM')", "الشهر"),
                 "txn_type": ("le.txn_type", "نوع الحركة"), "component": ("le.component", "المكوّن"),
                 "source_type": ("le.source_type", "المصدر")}


def rpt_custom(session: Session, p: Principal, params: dict) -> ReportData:
    """منشئ تقارير من أعمدة وتجميعات مسموح بها فقط (لا SQL حر، 09-reports RPT-20)."""
    fy = require_year(session, p, params)
    groups = params.get("group_by") or ["item"]
    if isinstance(groups, str):
        groups = [groups]
    bad = [g for g in groups if g not in CUSTOM_GROUPS]
    if bad or not groups:
        raise ValidationFailed("تجميع غير مسموح.", code="INVALID_GROUP", details={"allowed": list(CUSTOM_GROUPS)})
    sel = ", ".join(f"{CUSTOM_GROUPS[g][0]} AS g{i}" for i, g in enumerate(groups))
    conds, args = ["le.fiscal_year_id = :f"], {"f": fy.id}
    for key, cond in (("txn_type", "le.txn_type = :txn_type"), ("component", "le.component = :component")):
        if params.get(key):
            conds.append(cond)
            args[key] = params[key]
    if pdate(params, "date_from"):
        conds.append("le.entry_date >= :df")
        args["df"] = pdate(params, "date_from")
    if pdate(params, "date_to"):
        conds.append("le.entry_date <= :dt")
        args["dt"] = pdate(params, "date_to")
    for scope, col in (("ENTITY", "bl.entity_id"), ("ITEM", "bl.item_id")):
        if scope in p.scopes:
            conds.append(f"{col} = ANY(:{scope.lower()})")
            args[scope.lower()] = list(p.scopes[scope])
    gcols = ", ".join(f"g{i}" for i in range(len(groups)))
    sql = ("SELECT " + sel + ", sum(le.direction * le.amount) AS total, count(*) AS n FROM ledger_entries le "  # noqa: S608
           "JOIN budget_lines bl ON bl.id = le.budget_line_id JOIN budget_items bi ON bi.id = bl.item_id WHERE "
           + " AND ".join(conds) + " GROUP BY " + gcols + " ORDER BY " + gcols)
    rows = []
    for r in session.execute(text(sql), args):
        row = {f"g{i}": (TXN.get(r[i], r[i]) if groups[i] == "txn_type" else r[i]) for i in range(len(groups))}
        rows.append(row | {"total": r.total, "n": r.n})
    return ReportData("RPT-20", "تقرير مخصص", [Column(f"g{i}", CUSTOM_GROUPS[g][1]) for i, g in enumerate(groups)] + [
        Column("total", "الصافي", M, True), Column("n", "عدد القيود", "int")], rows, params,
        subtitle=f"السنة المالية {fy.year} — تجميع: {'، '.join(CUSTOM_GROUPS[g][1] for g in groups)}")


for _d in [
    ReportDef("RPT-01", "تقرير موقف الاعتماد", "كل مكونات الرصيد لكل بند، أو الموقف في تاريخ محدد", rpt_position,
              ("fiscal_year_id", "entity_id", "as_of")),
    ReportDef("RPT-02", "تقرير تنفيذ الباب الثاني", "التقرير المركزي: الاعتماد والمناقلات والارتباطات والمصروف والرصيد",
              rpt_central, ("fiscal_year_id", "entity_id", "as_of")),
    ReportDef("RPT-03", "تقرير المصروفات", "المصروفات بالتاريخ والمستفيد وطريقة الدفع", rpt_expenditures,
              ("fiscal_year_id", "date_from", "date_to", "supplier_id", "status")),
    ReportDef("RPT-04", "تقرير الارتباطات", "القيمة والمسدد والملغى والقائم والعمر", rpt_commitments,
              ("fiscal_year_id", "open_only")),
    ReportDef("RPT-05", "تقرير المناقلات", "المناقلات من بند إلى بند", rpt_transfers),
    ReportDef("RPT-06", "تقرير التفويضات", "التفويضات والموزع وغير الموزع", rpt_authorizations),
    ReportDef("RPT-07", "تقرير الرصيد المتاح", "المتاح ومستوى الاستخدام لكل بند", rpt_available),
    ReportDef("RPT-08", "تقرير التجاوزات", "البنود المتجاوزة ومصدر التجاوز", rpt_overruns),
    ReportDef("RPT-09", "تقرير العمليات المعلقة", "المستندات في دورة الموافقة ومدة الانتظار", rpt_pending),
    ReportDef("RPT-10", "تقرير العمليات الملغاة والمعكوسة", "المستندات الملغاة والمرفوضة والمعكوسة وأسبابها",
              rpt_cancelled),
    ReportDef("RPT-11", "تقرير حركة بند", "كشف حساب البند بالرصيد المتحرك", rpt_item_movement,
              ("fiscal_year_id", "budget_line_id")),
    ReportDef("RPT-12", "تقرير السنة المالية", "ملخص مؤشرات السنة", rpt_fiscal_year),
    ReportDef("RPT-13", "التقرير الشهري", "حركة الشهر والموقف في نهايته", rpt_monthly, ("fiscal_year_id", "month")),
    ReportDef("RPT-14", "التقرير الربع سنوي", "حركة الربع والموقف في نهايته", rpt_quarterly,
              ("fiscal_year_id", "quarter")),
    ReportDef("RPT-15", "التقرير السنوي", "حركة السنة مع المقارنة بالسنة السابقة", rpt_annual),
    ReportDef("RPT-16", "سجل التدقيق", "من فعل ماذا ومتى ومن أين", rpt_audit, ("date_from", "date_to", "table_name"),
              "audit.view"),
    ReportDef("RPT-17", "كشف تفصيلي للحركة", "كل القيود بكل الحقول", rpt_ledger_detail,
              ("fiscal_year_id", "date_from", "date_to", "txn_type")),
    ReportDef("RPT-18", "المقارنة بين الاعتماد والمصروف", "الاعتماد والفعلي والفرق والنسبة", rpt_budget_vs_actual),
    ReportDef("RPT-19", "الحساب الختامي للباب", "الوفر والتجاوز والارتباطات المرحّلة", rpt_final_account),
    ReportDef("RPT-20", "تقرير مخصص", "تجميع القيود حسب البند أو الشهر أو النوع أو المكوّن", rpt_custom,
              ("fiscal_year_id", "group_by", "txn_type", "component", "date_from", "date_to")),
]:
    register(_d)

