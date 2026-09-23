"""محرك التقارير (09-reports): تعريفات في الكود، مصدرها القيود والأرصدة فقط، وعرض JSON وPDF وExcel وطباعة."""
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import NotFound, ValidationFailed
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalYear
from app.modules.fiscal.service import get_year
from app.modules.ledger import engine
from app.modules.ledger.engine import COMPONENTS, Position
from app.modules.ledger.models import BudgetBalance, BudgetLine, LedgerEntry

ZERO = Decimal("0")


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: str = "text"   # text | money | date | percent | int
    total: bool = False


@dataclass
class ReportData:
    code: str
    title: str
    columns: list[Column]
    rows: list[dict]
    params: dict
    subtitle: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def totals(self) -> dict:
        out = {}
        for c in self.columns:
            if c.total:
                out[c.key] = sum((r.get(c.key) or ZERO for r in self.rows), ZERO)
        return out

    def fingerprint(self) -> str:
        """بصمة المحتوى تظهر في تذييل التقرير المطبوع (09-reports §1)."""
        payload = json.dumps({"code": self.code, "params": self.params, "rows": self.rows}, default=str,
                             ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ReportDef:
    code: str
    title: str
    description: str
    run: Callable[[Session, Principal, dict], ReportData]
    params: tuple[str, ...] = ("fiscal_year_id",)
    permission: str = "reports.view"


REGISTRY: dict[str, ReportDef] = {}


def register(d: ReportDef) -> ReportDef:
    REGISTRY[d.code] = d
    return d


def get_def(code: str) -> ReportDef:
    d = REGISTRY.get(code)
    if d is None:
        raise NotFound("التقرير غير موجود.")
    return d


# ---------------------------------------------------------------------------
# أدوات مشتركة
# ---------------------------------------------------------------------------
def require_year(session: Session, p: Principal, params: dict) -> FiscalYear:
    fy_id = params.get("fiscal_year_id")
    if not fy_id:
        raise ValidationFailed("السنة المالية مطلوبة لهذا التقرير.", code="FISCAL_YEAR_REQUIRED")
    fy = get_year(session, uuid.UUID(str(fy_id)))
    p.require_scope("FISCAL_YEAR", fy.id)
    return fy


def scoped_lines(session: Session, p: Principal, fy: FiscalYear, params: dict):
    stmt = select(BudgetLine, BudgetItem).join(BudgetItem, BudgetItem.id == BudgetLine.item_id).where(
        BudgetLine.fiscal_year_id == fy.id)
    if params.get("entity_id"):
        stmt = stmt.where(BudgetLine.entity_id == uuid.UUID(str(params["entity_id"])))
    if params.get("chapter_id"):
        stmt = stmt.where(BudgetItem.chapter_id == uuid.UUID(str(params["chapter_id"])))
    if params.get("item_ids"):
        stmt = stmt.where(BudgetItem.id.in_([uuid.UUID(str(x)) for x in params["item_ids"]]))
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return list(session.execute(stmt.order_by(BudgetItem.display_order, BudgetItem.code)).tuples())


def positions(session: Session, p: Principal, fy: FiscalYear, params: dict, as_of: date | None = None,
              since: date | None = None) -> list[tuple[BudgetLine, BudgetItem, Position]]:
    """موقف كل سطر: من ذاكرة الأرصدة، أو من القيود حتى تاريخ (as_of) أو بين تاريخين (since..as_of)."""
    lines = scoped_lines(session, p, fy, params)
    if not lines:
        return []
    ids = [ln.id for ln, _ in lines]
    if as_of is None and since is None:
        bal = {b.budget_line_id: b for b in session.scalars(select(BudgetBalance).where(BudgetBalance.budget_line_id.in_(ids)))}
        return [(ln, it, engine.position_from_balance(fy, bal.get(ln.id))) for ln, it in lines]
    stmt = select(LedgerEntry.budget_line_id, LedgerEntry.component,
                  func.sum(LedgerEntry.direction * LedgerEntry.amount)).where(
        LedgerEntry.budget_line_id.in_(ids)).group_by(LedgerEntry.budget_line_id, LedgerEntry.component)
    if as_of:
        stmt = stmt.where(LedgerEntry.entry_date <= as_of)
    if since:
        stmt = stmt.where(LedgerEntry.entry_date >= since)
    sums: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for lid, comp, total in session.execute(stmt):
        sums[lid][comp] = total
    return [(ln, it, Position(fy.control_basis, fy.count_reservations,
                              **{c.lower(): sums[ln.id][c] for c in COMPONENTS})) for ln, it in lines]


def pdate(params: dict, key: str) -> date | None:
    v = params.get(key)
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def jsonable_row(row: dict) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = f"{v:.3f}" if abs(v.as_tuple().exponent) >= 3 or v == v.to_integral_value() else str(v)
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out
