import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalYear
from app.modules.fiscal.service import get_year
from app.modules.ledger import engine
from app.modules.ledger.models import BudgetBalance, BudgetLine, LedgerEntry, OverrideGrant
from app.modules.ledger.schemas import (
    CheckIn,
    CheckOut,
    EntryOut,
    LinePositionOut,
    OverrideGrantIn,
    OverrideGrantOut,
    PositionOut,
    TimelineRow,
)
from app.modules.users.models import RolePermission, User, UserRole
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(tags=["الرقابة على الميزانية"])

WARNING_RATE, CRITICAL_RATE = Decimal("80"), Decimal("95")  # FR-AL-01 (قابلة للإعداد لاحقًا)


def level_of(p: engine.Position) -> str:
    if p.available < 0:
        return "EXCEEDED"
    rate = p.utilization_rate
    if rate is None:
        return "NO_BUDGET" if p.actual == 0 and p.commitment == 0 else "EXCEEDED"
    if rate >= 100:
        return "EXHAUSTED"
    if rate >= CRITICAL_RATE:
        return "CRITICAL"
    if rate >= WARNING_RATE:
        return "WARNING"
    return "NORMAL"


def _pos(p: engine.Position) -> PositionOut:
    return PositionOut(**p.as_dict())


def _scoped_line(session: Session, p: Principal, line_id: uuid.UUID) -> BudgetLine:
    line = engine.get_line(session, line_id)
    p.require_scope("FISCAL_YEAR", line.fiscal_year_id)
    p.require_scope("ENTITY", line.entity_id)
    p.require_scope("ITEM", line.item_id)
    return line


def _entry_out(e: LedgerEntry, item_code: str, **extra) -> dict:
    return dict(id=e.id, entry_no=e.entry_no, entry_date=e.entry_date, date_is_estimated=e.date_is_estimated,
                budget_line_id=e.budget_line_id, item_code=item_code, txn_type=e.txn_type, component=e.component,
                direction=e.direction, amount=e.amount, signed_amount=e.direction * e.amount,
                source_type=e.source_type, source_id=e.source_id, document_no=e.document_no,
                description=e.description, transfer_group_id=e.transfer_group_id, reversal_of_id=e.reversal_of_id,
                is_historical_exception=e.is_historical_exception, override_grant_id=e.override_grant_id,
                posted_by=e.posted_by, posted_at=e.posted_at, **extra)


@router.get("/budget-position", response_model=list[LinePositionOut])
def budget_position(fiscal_year_id: uuid.UUID, entity_id: uuid.UUID | None = None,
                    chapter_id: uuid.UUID | None = None, p: Principal = Depends(require("budget.view")),
                    session: Session = Depends(get_session)):
    """موقف كل البنود القابلة للترحيل في السنة (أساس التقرير المركزي RPT-02)."""
    fy = get_year(session, fiscal_year_id)
    p.require_scope("FISCAL_YEAR", fy.id)
    items_stmt = select(BudgetItem).where(BudgetItem.is_postable.is_(True)).order_by(BudgetItem.display_order,
                                                                                      BudgetItem.code)
    if chapter_id:
        items_stmt = items_stmt.where(BudgetItem.chapter_id == chapter_id)
    if "ITEM" in p.scopes:
        items_stmt = items_stmt.where(BudgetItem.id.in_(p.scopes["ITEM"]))
    lines_stmt = select(BudgetLine, BudgetBalance).join(BudgetBalance).where(BudgetLine.fiscal_year_id == fy.id)
    if entity_id:
        lines_stmt = lines_stmt.where(BudgetLine.entity_id == entity_id)
    if "ENTITY" in p.scopes:
        lines_stmt = lines_stmt.where(BudgetLine.entity_id.in_(p.scopes["ENTITY"]))
    by_item: dict[uuid.UUID, list] = {}
    for line, bal in session.execute(lines_stmt):
        by_item.setdefault(line.item_id, []).append((line, bal))
    out = []
    for item in session.scalars(items_stmt):
        rows = by_item.get(item.id)
        if not rows:
            if not item.is_active:
                continue
            pos = engine.position_from_balance(fy, None)
            out.append(LinePositionOut(budget_line_id=None, fiscal_year=fy.year, entity_id=entity_id or uuid.UUID(int=0),
                                       item_id=item.id, item_code=item.code, item_name=item.name,
                                       level=level_of(pos), position=_pos(pos)))
            continue
        for line, bal in rows:
            pos = engine.position_from_balance(fy, bal)
            out.append(LinePositionOut(budget_line_id=line.id, fiscal_year=fy.year, entity_id=line.entity_id,
                                       item_id=item.id, item_code=item.code, item_name=item.name,
                                       level=level_of(pos), position=_pos(pos)))
    return out


@router.get("/budget-lines/{line_id}", response_model=LinePositionOut)
def line_position(line_id: uuid.UUID, as_of: date | None = None, p: Principal = Depends(require("budget.view")),
                  session: Session = Depends(get_session)):
    line = _scoped_line(session, p, line_id)
    pos = engine.position_as_of(session, line, as_of) if as_of else engine.current_position(session, line)
    item = session.get(BudgetItem, line.item_id)
    fy = session.get(FiscalYear, line.fiscal_year_id)
    return LinePositionOut(budget_line_id=line.id, fiscal_year=fy.year, entity_id=line.entity_id, item_id=item.id,
                           item_code=item.code, item_name=item.name, level=level_of(pos), position=_pos(pos))


@router.get("/budget-lines/{line_id}/timeline", response_model=list[TimelineRow])
def timeline(line_id: uuid.UUID, p: Principal = Depends(require("budget.view")),
             session: Session = Depends(get_session)):
    """التسلسل الزمني لكل حركات البند مع الرصيد المتاح بعد كل قيد (07-ui §5)."""
    line = _scoped_line(session, p, line_id)
    fy = session.get(FiscalYear, line.fiscal_year_id)
    code = session.get(BudgetItem, line.item_id).code
    pos = engine.Position(fy.control_basis, fy.count_reservations)
    rows = []
    for e in session.scalars(select(LedgerEntry).where(LedgerEntry.budget_line_id == line.id)
                             .order_by(LedgerEntry.entry_date, LedgerEntry.entry_no)):
        pos = pos.with_deltas({e.component: e.direction * e.amount})
        rows.append(TimelineRow(**_entry_out(e, code, available_after=pos.available)))
    return rows


@router.post("/budget-lines/{line_id}/check", response_model=CheckOut)
def check(line_id: uuid.UUID, body: CheckIn, p: Principal = Depends(require("budget.view")),
          session: Session = Depends(get_session)):
    line = _scoped_line(session, p, line_id)
    return CheckOut(**engine.check_amount(engine.current_position(session, line), body.amount).as_dict())


@router.get("/ledger-entries", response_model=Page[EntryOut])
def ledger_entries(fiscal_year_id: uuid.UUID | None = None, budget_line_id: uuid.UUID | None = None,
                   txn_type: str | None = None, source_type: str | None = None, source_id: uuid.UUID | None = None,
                   document_no: str | None = Query(default=None, max_length=50), date_from: date | None = None,
                   date_to: date | None = None, params: PageParams = Depends(),
                   p: Principal = Depends(require("budget.view")), session: Session = Depends(get_session)):
    stmt = (select(LedgerEntry, BudgetItem.code).join(BudgetLine, BudgetLine.id == LedgerEntry.budget_line_id)
            .join(BudgetItem, BudgetItem.id == BudgetLine.item_id))
    filters = {
        LedgerEntry.fiscal_year_id: fiscal_year_id, LedgerEntry.budget_line_id: budget_line_id,
        LedgerEntry.txn_type: txn_type, LedgerEntry.source_type: source_type, LedgerEntry.source_id: source_id,
        LedgerEntry.document_no: document_no,
    }
    for col, value in filters.items():
        if value is not None:
            stmt = stmt.where(col == value)
    if date_from:
        stmt = stmt.where(LedgerEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(LedgerEntry.entry_date <= date_to)
    for scope, col in (("FISCAL_YEAR", BudgetLine.fiscal_year_id), ("ENTITY", BudgetLine.entity_id),
                       ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    stmt = stmt.order_by(LedgerEntry.entry_no.desc())
    return paginate(session, stmt, params, lambda r: EntryOut(**_entry_out(r[0], r[1])))


@router.post("/ledger/reconcile")
def reconcile(p: Principal = Depends(require("budget.reconcile")), session: Session = Depends(get_session)):
    mismatched = engine.reconcile(session)
    return {"ok": not mismatched, "mismatched_lines": [str(x) for x in mismatched],
            "checked_at": datetime.now(UTC).isoformat()}


# --- منح الاستثناء (D-10) -----------------------------------------------------
@router.get("/override-grants", response_model=list[OverrideGrantOut])
def list_grants(p: Principal = Depends(require("budget.view")), session: Session = Depends(get_session)):
    stmt = select(OverrideGrant).order_by(OverrideGrant.created_at.desc())
    if not p.has("budget.override_grant") and not p.has("audit.view"):
        stmt = stmt.where(OverrideGrant.user_id == p.user_id)
    return [OverrideGrantOut.model_validate(g, from_attributes=True) for g in session.scalars(stmt)]


@router.post("/override-grants", response_model=OverrideGrantOut, status_code=201)
def create_grant(body: OverrideGrantIn, p: Principal = Depends(require("budget.override_grant")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    if body.user_id == p.user_id:
        raise ValidationFailed("لا يمكن منح استثناء لنفسك.", code="SELF_GRANT")
    if body.valid_to <= body.valid_from:
        raise ValidationFailed("مدة المنحة غير صالحة.", code="INVALID_RANGE")
    if len(body.reason.strip()) < 5:
        raise ValidationFailed("سبب المنحة مطلوب.", code="REASON_REQUIRED")
    grantee = session.get(User, body.user_id)
    if grantee is None or not grantee.is_active:
        raise NotFound("المستخدم غير موجود.")
    approve_perms = {f"{d}.approve" for d in ("budget_documents", "authorizations", "transfers", "commitments",
                                                "expenditures", "adjustments")}
    has_approve = session.scalar(select(RolePermission.permission_code).join(
        UserRole, UserRole.role_id == RolePermission.role_id).where(
        UserRole.user_id == grantee.id, RolePermission.permission_code.in_(approve_perms)).limit(1))
    if not has_approve:
        raise ValidationFailed("المنحة تُعطى لمستخدم يملك صلاحية الاعتماد النهائي فقط.", code="GRANTEE_NOT_APPROVER")
    if body.budget_line_id:
        engine.get_line(session, body.budget_line_id)
    begin_write(session, p.user_id, meta, reason=body.reason)
    g = OverrideGrant(user_id=body.user_id, budget_line_id=body.budget_line_id, max_amount=body.max_amount,
                      valid_from=body.valid_from, valid_to=body.valid_to, reason=body.reason.strip(),
                      granted_by=p.user_id)
    session.add(g)
    session.commit()
    return OverrideGrantOut.model_validate(g, from_attributes=True)


@router.post("/override-grants/{grant_id}/revoke", response_model=OverrideGrantOut)
def revoke_grant(grant_id: uuid.UUID, p: Principal = Depends(require("budget.override_grant")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    g = session.get(OverrideGrant, grant_id, with_for_update=True)
    if g is None:
        raise NotFound("المنحة غير موجودة.")
    if g.revoked_at:
        raise Conflict("المنحة ملغاة مسبقًا.", code="INVALID_STATE")
    g.revoked_at = datetime.now(UTC)
    session.commit()
    return OverrideGrantOut.model_validate(g, from_attributes=True)
