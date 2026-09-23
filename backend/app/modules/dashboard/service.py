"""لوحة القيادة والتحليل البصري (07-ui §4). كل الأرقام من الأرصدة والقيود، لا من إجماليات مخزنة."""
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.modules.alerts.models import Alert
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalYear
from app.modules.fiscal.service import get_year
from app.modules.ledger import engine
from app.modules.ledger.models import BudgetBalance, BudgetLine, LedgerEntry

ZERO = Decimal("0")
DOC_TABLES = ("budget_documents", "authorizations", "transfers", "commitments", "expenditures", "adjustments")


def _lines(session: Session, p: Principal, fy: FiscalYear, entity_id: uuid.UUID | None = None):
    stmt = select(BudgetLine, BudgetBalance, BudgetItem).join(BudgetBalance).join(
        BudgetItem, BudgetItem.id == BudgetLine.item_id).where(BudgetLine.fiscal_year_id == fy.id)
    if entity_id:
        stmt = stmt.where(BudgetLine.entity_id == entity_id)
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return [(ln, engine.position_from_balance(fy, bal), item)
            for ln, bal, item in session.execute(stmt.order_by(BudgetItem.display_order, BudgetItem.code))]


def kpis(session: Session, p: Principal, fiscal_year_id: uuid.UUID, entity_id: uuid.UUID | None = None) -> dict:
    fy = get_year(session, fiscal_year_id)
    rows = _lines(session, p, fy, entity_id)
    budget = sum((pos.adjusted_budget for _, pos, _ in rows), ZERO)
    actual = sum((pos.actual for _, pos, _ in rows), ZERO)
    commitments = sum((pos.commitment + pos.counted_reservation for _, pos, _ in rows), ZERO)
    available = sum((pos.available for _, pos, _ in rows), ZERO)
    from sqlalchemy import text
    pending = sum(session.scalar(text(
        f"SELECT count(*) FROM {t} WHERE fiscal_year_id = :f AND status IN ('SUBMITTED','IN_REVIEW')"),  # noqa: S608
        {"f": fy.id}) for t in DOC_TABLES)
    transfers = session.scalar(text("SELECT count(*) FROM transfers WHERE fiscal_year_id=:f AND status='POSTED'"),
                               {"f": fy.id})
    from app.modules.workflow.service import inbox
    return {
        "fiscal_year": fy.year,
        "total_budget": budget, "total_actual": actual, "total_commitments": commitments,
        "total_available": available,
        "execution_rate": (actual * 100 / budget).quantize(Decimal("0.01")) if budget > 0 else None,
        "utilization_rate": ((actual + commitments) * 100 / budget).quantize(Decimal("0.01")) if budget > 0 else None,
        "transfers_count": transfers, "pending_count": pending,
        "exceeded_count": sum(1 for _, pos, _ in rows if pos.available < 0),
        "open_alerts": session.scalar(select(func.count()).select_from(Alert).where(
            Alert.fiscal_year_id == fy.id, Alert.status.in_(("OPEN", "ACKNOWLEDGED")))),
        "awaiting_my_action": len(inbox(session, p)) if p.has("workflow.inbox") else 0,
    }


def _item_row(item: BudgetItem, pos: engine.Position) -> dict:
    return {"item_code": item.code, "item_name": item.name, "budget": pos.adjusted_budget, "actual": pos.actual,
            "commitment": pos.commitment + pos.counted_reservation, "available": pos.available,
            "utilization_rate": pos.utilization_rate, "actual_rate": pos.actual_rate}


def chart(session: Session, p: Principal, name: str, fiscal_year_id: uuid.UUID, entity_id: uuid.UUID | None = None):
    fy = get_year(session, fiscal_year_id)
    if name in ("budget_vs_actual", "budget_vs_commitment", "available", "item_utilization"):
        return [_item_row(item, pos) for _, pos, item in _lines(session, p, fy, entity_id)
                if pos.adjusted_budget or pos.actual or pos.commitment]
    if name == "top_spending":
        rows = [_item_row(item, pos) for _, pos, item in _lines(session, p, fy, entity_id) if pos.actual > 0]
        return sorted(rows, key=lambda r: r["actual"], reverse=True)[:10]
    if name == "low_balance":
        rows = [_item_row(item, pos) for _, pos, item in _lines(session, p, fy, entity_id)
                if pos.available >= 0 and pos.utilization_rate is not None and pos.utilization_rate >= 80]
        return sorted(rows, key=lambda r: r["utilization_rate"], reverse=True)
    if name == "over_budget":
        rows = [_item_row(item, pos) | {"excess": -pos.available}
                for _, pos, item in _lines(session, p, fy, entity_id) if pos.available < 0]
        return sorted(rows, key=lambda r: r["excess"], reverse=True)
    if name == "monthly_expenditure":
        month = extract("month", LedgerEntry.entry_date)
        stmt = select(month, LedgerEntry.date_is_estimated, func.sum(LedgerEntry.direction * LedgerEntry.amount)).join(
            BudgetLine, BudgetLine.id == LedgerEntry.budget_line_id).where(
            LedgerEntry.fiscal_year_id == fy.id, LedgerEntry.component == "ACTUAL").group_by(month, LedgerEntry.date_is_estimated)
        for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
            if scope in p.scopes:
                stmt = stmt.where(col.in_(p.scopes[scope]))
        if entity_id:
            stmt = stmt.where(BudgetLine.entity_id == entity_id)
        by_month, estimated = defaultdict(lambda: ZERO), ZERO
        for m, est, total in session.execute(stmt):
            if est:
                estimated += total   # D-04: الحركات بتاريخ تقديري في سطر منفصل
            else:
                by_month[int(m)] += total
        out, running = [], ZERO
        for m in range(1, 13):
            running += by_month[m]
            out.append({"month": m, "actual": by_month[m], "cumulative": running})
        return {"months": out, "estimated_dates_total": estimated}
    if name == "transfer_analysis":
        from sqlalchemy import text
        rows = session.execute(text("""
            SELECT fi.code AS from_code, ti.code AS to_code, sum(tl.amount) AS amount, count(*) AS n
            FROM transfer_lines tl JOIN transfers t ON t.id = tl.transfer_id
            JOIN budget_lines fl ON fl.id = tl.from_line_id JOIN budget_items fi ON fi.id = fl.item_id
            JOIN budget_lines tl2 ON tl2.id = tl.to_line_id JOIN budget_items ti ON ti.id = tl2.item_id
            WHERE t.fiscal_year_id = :f AND t.status = 'POSTED' GROUP BY 1, 2 ORDER BY 3 DESC"""), {"f": fy.id})
        return [dict(r._mapping) for r in rows]
    if name == "year_over_year":
        out = []
        for y in session.scalars(select(FiscalYear).order_by(FiscalYear.year)):
            if "FISCAL_YEAR" in p.scopes and y.id not in p.scopes["FISCAL_YEAR"]:
                continue
            rows = _lines(session, p, y, entity_id)
            out.append({"year": y.year, "budget": sum((pos.adjusted_budget for _, pos, _ in rows), ZERO),
                        "actual": sum((pos.actual for _, pos, _ in rows), ZERO)})
        return out
    from app.core.errors import NotFound
    raise NotFound("الرسم غير معروف.")


CHARTS = ["budget_vs_actual", "budget_vs_commitment", "available", "monthly_expenditure", "item_utilization",
          "transfer_analysis", "year_over_year", "top_spending", "low_balance", "over_budget"]
