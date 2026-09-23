"""البحث الشامل (FR-SR): رقم المستند، ورقم التفويض/المناقلة، والبند، والجهة، والمورد، والسنة،
والقيمة، والتاريخ، والمستخدم. نص عربي مطبَّع (ar_normalize) ومقيد بصلاحيات المستخدم ونطاقه."""
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.shared.arabic import normalize

# (نوع المصدر، الصلاحية، استعلام يعيد: id, doc_no, doc_date, amount, status, description, fiscal_year_id, created_by)
SOURCES = {
    "budget_document": ("budget_documents.view", """
        SELECT d.id, d.doc_no, d.doc_date, (SELECT sum(amount) FROM budget_document_lines WHERE document_id = d.id),
               d.status, d.description, d.fiscal_year_id, d.created_by, NULL::uuid AS line_id FROM budget_documents d"""),
    "authorization": ("authorizations.view", """
        SELECT a.id, a.auth_no, a.auth_date, a.amount, a.status, a.purpose, a.fiscal_year_id, a.created_by,
               NULL::uuid FROM authorizations a"""),
    "transfer": ("transfers.view", """
        SELECT t.id, t.transfer_no, t.transfer_date, (SELECT sum(amount) FROM transfer_lines WHERE transfer_id = t.id),
               t.status, t.reason || ' ' || coalesce(t.approval_no, ''), t.fiscal_year_id, t.created_by, NULL::uuid
        FROM transfers t"""),
    "commitment": ("commitments.view", """
        SELECT c.id, c.commitment_no, c.commitment_date, c.amount, c.status,
               c.description || ' ' || coalesce(s.name, '') || ' ' || coalesce(c.reference, ''), c.fiscal_year_id,
               c.created_by, c.budget_line_id
        FROM commitments c LEFT JOIN suppliers s ON s.id = c.supplier_id"""),
    "expenditure": ("expenditures.view", """
        SELECT e.id, e.document_no, e.expenditure_date, e.amount, e.status,
               e.description || ' ' || coalesce(s.name, '') || ' ' || coalesce(e.payment_order_no, '') || ' '
               || coalesce(e.cheque_no, ''), e.fiscal_year_id, e.created_by, e.budget_line_id
        FROM expenditures e LEFT JOIN suppliers s ON s.id = e.supplier_id"""),
    "adjustment": ("adjustments.view", """
        SELECT a.id, a.adjustment_no, a.adjustment_date, a.cancel_amount, a.status, a.reason, a.fiscal_year_id,
               a.created_by, NULL::uuid FROM adjustments a"""),
}

_ITEM_CODE = re.compile(r"^\d+\s*/\s*\d+(\s*/\s*\d+)*$")


def _as_amount(q: str) -> Decimal | None:
    try:
        return Decimal(q.replace(",", "").replace("٬", ""))
    except (InvalidOperation, ValueError):
        return None


def search(session: Session, p: Principal, q: str | None, *, types: list[str] | None = None,
           fiscal_year_id: uuid.UUID | None = None, amount_min: Decimal | None = None,
           amount_max: Decimal | None = None, date_from: date | None = None, date_to: date | None = None,
           user_id: uuid.UUID | None = None, limit: int = 50) -> dict:
    q = (q or "").strip()
    norm = normalize(q)
    amount = _as_amount(q) if q else None
    item_code = q.replace(" ", "") if q and _ITEM_CODE.match(q) else None
    results = []
    for stype, (perm, base) in SOURCES.items():
        if (types and stype not in types) or not p.has(perm):
            continue
        conds, params = [], {"norm": f"%{norm}%", "q": q, "limit": limit}
        if q:
            ors = ["ar_normalize(x.doc_no) LIKE :norm", "ar_normalize(x.description) LIKE :norm"]
            if amount is not None:
                ors.append("x.amount = :amount")
                params["amount"] = amount
            if item_code:
                ors.append("x.line_id IN (SELECT bl.id FROM budget_lines bl JOIN budget_items bi ON bi.id = bl.item_id"
                           " WHERE bi.code = :item_code)")
                params["item_code"] = item_code
            ors.append("x.created_by IN (SELECT id FROM users WHERE ar_normalize(full_name) LIKE :norm"
                       " OR lower(username::text) = lower(:q))")
            conds.append("(" + " OR ".join(ors) + ")")
        for cond, key, value in (("x.fiscal_year_id = :fy", "fy", fiscal_year_id), ("x.amount >= :amin", "amin", amount_min),
                                 ("x.amount <= :amax", "amax", amount_max), ("x.doc_date >= :dfrom", "dfrom", date_from),
                                 ("x.doc_date <= :dto", "dto", date_to), ("x.created_by = :uid", "uid", user_id)):
            if value is not None:
                conds.append(cond)
                params[key] = value
        if "FISCAL_YEAR" in p.scopes:
            conds.append("x.fiscal_year_id = ANY(:fys)")
            params["fys"] = list(p.scopes["FISCAL_YEAR"])
        for scope, col in (("ITEM", "item_id"), ("ENTITY", "entity_id")):
            if scope in p.scopes:
                # col من مجموعة ثابتة في الكود؛ قيم المستخدم معاملات مربوطة فقط
                conds.append(f"(x.line_id IS NULL OR x.line_id IN (SELECT id FROM budget_lines WHERE {col} = ANY(:{col}s)))")  # noqa: S608
                params[f"{col}s"] = list(p.scopes[scope])
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        # base وwhere أجزاء ثابتة من الكود؛ مدخلات المستخدم كلها في params
        sql = ("SELECT * FROM (" + base + ") AS x(id, doc_no, doc_date, amount, status, description,"  # noqa: S608
               " fiscal_year_id, created_by, line_id)" + where + " ORDER BY doc_date DESC LIMIT :limit")
        for r in session.execute(text(sql), params):
            results.append({"type": stype, "id": r.id, "doc_no": r.doc_no, "date": r.doc_date, "amount": r.amount,
                            "status": r.status, "description": r.description, "fiscal_year_id": r.fiscal_year_id})
    items, suppliers = [], []
    if q and p.has("catalog.view"):
        items = [dict(r._mapping) for r in session.execute(text(
            "SELECT id, code, name FROM budget_items WHERE code = :code OR ar_normalize(name) LIKE :norm"
            " ORDER BY display_order LIMIT 20"), {"code": item_code or q, "norm": f"%{norm}%"})]
        suppliers = [dict(r._mapping) for r in session.execute(text(
            "SELECT id, name FROM suppliers WHERE name_normalized LIKE :n OR similarity(name_normalized, :raw) > 0.4"
            " ORDER BY similarity(name_normalized, :raw) DESC LIMIT 20"), {"n": f"%{norm}%", "raw": norm})]
    results.sort(key=lambda r: r["date"] or date.min, reverse=True)
    return {"documents": results[:limit], "items": items, "suppliers": suppliers}
