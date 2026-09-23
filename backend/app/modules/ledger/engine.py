"""محرك الحركات والرقابة على الميزانية (03-financial-model).

القاعدة: لا وحدة تكتب في ledger_entries إلا هذه الوحدة، ولا يكتب أحد في budget_balances إلا
trigger دفتر الحركات. كل المستندات تُرحَّل عبر post().

فحص التوفر آلي وعام: لكل سطر ميزانية يتأثر بالترحيل يُحسب أثر القيود على «الرصيد المتاح»
(وعلى «الاعتماد غير المفوَّض» في نمط المستويين). إذا كان الأثر سالبًا (استهلاك) يجب أن يكفي
الرصيد. هذا يغطي تلقائيًا: المناقلة الصادرة، والارتباط، والصرف (بعد خصم تسييل الارتباط)،
والتخفيض، وعكس مناقلة واردة، والتسويات.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from app.modules.catalog.models import BudgetItem, Entity
from app.modules.fiscal.models import FiscalYear
from app.modules.fiscal.service import period_for_date
from app.modules.ledger.models import BudgetBalance, BudgetLine, LedgerEntry, OverrideGrant
from app.shared.money import ZERO, fmt

INSUFFICIENT_MESSAGE = "لا يوجد اعتماد متاح كافٍ لهذه العملية."

COMPONENTS = ("APPROPRIATION", "ALLOCATION", "TRANSFER_IN", "TRANSFER_OUT", "RESERVATION", "COMMITMENT", "ACTUAL")
CLOSING_TYPES = {"CLOSING", "CARRY_FORWARD", "RESERVATION_RELEASE"}


class InsufficientBudget(DomainError):
    status_code = 409
    code = "INSUFFICIENT_BUDGET"


# ---------------------------------------------------------------------------
# الموقف (Position)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Position:
    """مكونات الرصيد والمعادلات (03-financial-model §3). كل الأرقام Decimal."""
    control_basis: str
    count_reservations: bool
    appropriation: Decimal = ZERO
    allocation: Decimal = ZERO
    transfer_in: Decimal = ZERO
    transfer_out: Decimal = ZERO
    reservation: Decimal = ZERO
    commitment: Decimal = ZERO
    actual: Decimal = ZERO

    @property
    def control_base(self) -> Decimal:
        return self.appropriation if self.control_basis == "APPROPRIATION" else self.allocation

    @property
    def adjusted_budget(self) -> Decimal:
        return self.control_base + self.transfer_in - self.transfer_out

    @property
    def counted_reservation(self) -> Decimal:
        return self.reservation if self.count_reservations else ZERO

    @property
    def book_balance(self) -> Decimal:
        return self.adjusted_budget - self.actual

    @property
    def available(self) -> Decimal:
        return self.adjusted_budget - self.actual - self.commitment - self.counted_reservation

    @property
    def unallocated(self) -> Decimal | None:
        """الاعتماد غير المفوَّض (نمط المستويين فقط)."""
        return self.appropriation - self.allocation if self.control_basis == "TWO_LEVEL" else None

    @property
    def actual_rate(self) -> Decimal | None:
        """نسبة التنفيذ الرسمية = الفعلي ÷ الاعتماد بعد المناقلات (D-13)."""
        b = self.adjusted_budget
        return (self.actual * 100 / b).quantize(Decimal("0.01")) if b > 0 else None

    @property
    def utilization_rate(self) -> Decimal | None:
        b = self.adjusted_budget
        used = self.actual + self.commitment + self.counted_reservation
        return (used * 100 / b).quantize(Decimal("0.01")) if b > 0 else None

    def with_deltas(self, deltas: dict[str, Decimal]) -> "Position":
        return Position(self.control_basis, self.count_reservations,
                        **{c.lower(): getattr(self, c.lower()) + deltas.get(c, ZERO) for c in COMPONENTS})

    def as_dict(self) -> dict:
        return {
            "control_basis": self.control_basis,
            "appropriation": self.appropriation, "allocation": self.allocation,
            "transfer_in": self.transfer_in, "transfer_out": self.transfer_out,
            "reservation": self.reservation, "commitment": self.commitment, "actual": self.actual,
            "control_base": self.control_base, "adjusted_budget": self.adjusted_budget,
            "book_balance": self.book_balance, "available": self.available, "unallocated": self.unallocated,
            "actual_rate": self.actual_rate, "utilization_rate": self.utilization_rate,
        }


def position_from_balance(fy: FiscalYear, b: BudgetBalance | None) -> Position:
    if b is None:
        return Position(fy.control_basis, fy.count_reservations)
    return Position(fy.control_basis, fy.count_reservations, b.appropriation, b.allocation, b.transfer_in,
                    b.transfer_out, b.reservation, b.commitment, b.actual)


def position_as_of(session: Session, line: BudgetLine, as_of: date | None) -> Position:
    """الموقف من القيود مباشرة (وليس من ذاكرة الأرصدة) حتى تاريخ معين."""
    fy = session.get(FiscalYear, line.fiscal_year_id)
    stmt = select(LedgerEntry.component, LedgerEntry.direction, LedgerEntry.amount).where(
        LedgerEntry.budget_line_id == line.id)
    if as_of:
        stmt = stmt.where(LedgerEntry.entry_date <= as_of)
    sums: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for comp, d, amt in session.execute(stmt):
        sums[comp] += d * amt
    return Position(fy.control_basis, fy.count_reservations, **{c.lower(): sums[c] for c in COMPONENTS})


# ---------------------------------------------------------------------------
# أسطر الميزانية
# ---------------------------------------------------------------------------
def get_line(session: Session, line_id: uuid.UUID) -> BudgetLine:
    line = session.get(BudgetLine, line_id)
    if line is None:
        raise NotFound("سطر الميزانية غير موجود.")
    return line


def find_line(session: Session, fiscal_year_id: uuid.UUID, entity_id: uuid.UUID, item_id: uuid.UUID) -> BudgetLine | None:
    return session.scalar(select(BudgetLine).where(BudgetLine.fiscal_year_id == fiscal_year_id,
                                                   BudgetLine.entity_id == entity_id, BudgetLine.item_id == item_id))


def get_or_create_line(session: Session, fiscal_year_id: uuid.UUID, entity_id: uuid.UUID,
                       item_id: uuid.UUID) -> BudgetLine:
    line = find_line(session, fiscal_year_id, entity_id, item_id)
    if line is not None:
        return line
    item = session.get(BudgetItem, item_id)
    if item is None:
        raise ValidationFailed("البند غير موجود.", code="UNKNOWN_ITEM")
    if not item.is_active or not item.is_postable:
        raise ValidationFailed(f"البند {item.code} غير نشط أو غير قابل للترحيل.", code="ITEM_NOT_POSTABLE")
    entity = session.get(Entity, entity_id)
    if entity is None or not entity.is_active:
        raise ValidationFailed("الجهة غير موجودة أو غير نشطة.", code="UNKNOWN_ENTITY")
    if session.get(FiscalYear, fiscal_year_id) is None:
        raise ValidationFailed("السنة المالية غير موجودة.", code="UNKNOWN_YEAR")
    line = BudgetLine(id=uuid.uuid4(), fiscal_year_id=fiscal_year_id, entity_id=entity_id, item_id=item_id)
    session.add(line)
    session.flush()
    return line


def line_label(session: Session, line: BudgetLine) -> dict:
    item = session.get(BudgetItem, line.item_id)
    fy = session.get(FiscalYear, line.fiscal_year_id)
    return {"budget_line_id": str(line.id), "fiscal_year": fy.year, "item_code": item.code, "item_name": item.name}


def current_position(session: Session, line: BudgetLine) -> Position:
    fy = session.get(FiscalYear, line.fiscal_year_id)
    return position_from_balance(fy, session.get(BudgetBalance, line.id))


# ---------------------------------------------------------------------------
# فحص التوفر
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    ok: bool
    available: Decimal
    requested: Decimal
    shortfall: Decimal
    message: str | None = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "available": self.available, "requested": self.requested,
                "shortfall": self.shortfall, "message": self.message}


def check_amount(position: Position, requested: Decimal) -> CheckResult:
    """فحص معلوماتي (بدون قفل): هل يكفي المتاح لاستهلاك مبلغ معين؟"""
    shortfall = max(requested - max(position.available, ZERO), ZERO)
    return CheckResult(shortfall == 0, position.available, requested, shortfall,
                       None if shortfall == 0 else INSUFFICIENT_MESSAGE)


# ---------------------------------------------------------------------------
# الترحيل
# ---------------------------------------------------------------------------
@dataclass
class EntrySpec:
    budget_line_id: uuid.UUID
    txn_type: str
    component: str
    direction: int
    amount: Decimal
    source_line_id: uuid.UUID | None = None
    transfer_group_id: uuid.UUID | None = None
    reversal_of_id: uuid.UUID | None = None
    description: str | None = None


@dataclass
class PostingRequest:
    fiscal_year_id: uuid.UUID
    entry_date: date
    source_type: str
    source_id: uuid.UUID
    posted_by: uuid.UUID
    entries: list[EntrySpec]
    document_no: str | None = None
    approved_by: uuid.UUID | None = None
    override_grant_id: uuid.UUID | None = None
    date_is_estimated: bool = False
    # للاستيراد التاريخي فقط (D-02): يسمح بالرصيد السالب ويوسم القيود
    historical_exception: bool = False
    allow_adjustment_period: bool = False


@dataclass
class PostingResult:
    entries: list[LedgerEntry]
    positions_before: dict[uuid.UUID, Position]
    positions_after: dict[uuid.UUID, Position]
    override_used: Decimal = ZERO
    checks: list[dict] = field(default_factory=list)


def _deltas(entries: list[EntrySpec]) -> dict[uuid.UUID, dict[str, Decimal]]:
    out: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for e in entries:
        out[e.budget_line_id][e.component] += e.direction * e.amount
    return out


def _validate_specs(req: PostingRequest) -> None:
    if not req.entries:
        raise ValidationFailed("لا توجد قيود للترحيل.", code="EMPTY_POSTING")
    for e in req.entries:
        if e.component not in COMPONENTS:
            raise ValidationFailed("مكوّن رصيد غير معروف.", code="UNKNOWN_COMPONENT")
        if e.direction not in (1, -1):
            raise ValidationFailed("اتجاه القيد غير صالح.", code="INVALID_DIRECTION")
        if e.amount <= 0:
            raise ValidationFailed("مبلغ القيد يجب أن يكون أكبر من صفر.", code="INVALID_AMOUNT")


def _load_override(session: Session, req: PostingRequest) -> OverrideGrant | None:
    if req.override_grant_id is None:
        return None
    g = session.get(OverrideGrant, req.override_grant_id, with_for_update=True)
    now = datetime.now(UTC)
    if (g is None or g.revoked_at is not None or not (g.valid_from <= now <= g.valid_to)
            or g.user_id != (req.approved_by or req.posted_by)):
        raise Conflict("منحة الاستثناء غير صالحة أو منتهية أو لا تخصك.", code="INVALID_OVERRIDE")
    return g


def post(session: Session, req: PostingRequest) -> PostingResult:
    """ترحيل ذري: قفل الأسطر ← فحص ← إدراج القيود (والـ trigger يحدّث الأرصدة).

    يجب أن يكون المستدعي قد ضبط سياق التدقيق للمعاملة. لا يُنفذ COMMIT هنا.
    """
    _validate_specs(req)
    fy = session.get(FiscalYear, req.fiscal_year_id)
    if fy is None:
        raise NotFound("السنة المالية غير موجودة.")
    types = {e.txn_type for e in req.entries}
    if fy.status == "CLOSING" and not types <= CLOSING_TYPES:
        raise Conflict(f"السنة المالية {fy.year} قيد الإقفال.", code="YEAR_CLOSING")
    if fy.status not in ("OPEN", "CLOSING"):
        raise Conflict(f"السنة المالية {fy.year} غير مفتوحة للترحيل.", code="YEAR_NOT_OPEN")
    period = period_for_date(session, fy, req.entry_date, allow_adjustment=req.allow_adjustment_period)
    if period.status != "OPEN":
        raise Conflict(f"الفترة المالية {period.period_no} مقفلة.", code="PERIOD_CLOSED")

    line_ids = sorted({e.budget_line_id for e in req.entries})
    # قفل بترتيب ثابت لتجنب الجمود
    balances = {b.budget_line_id: b for b in session.scalars(
        select(BudgetBalance).where(BudgetBalance.budget_line_id.in_(line_ids))
        .order_by(BudgetBalance.budget_line_id).with_for_update())}
    lines = {ln.id: ln for ln in session.scalars(select(BudgetLine).where(BudgetLine.id.in_(line_ids)))}
    for lid in line_ids:
        line = lines.get(lid)
        if line is None:
            raise NotFound("سطر الميزانية غير موجود.")
        if line.fiscal_year_id != fy.id:
            raise ValidationFailed("سطر الميزانية لا ينتمي للسنة المالية للمستند.", code="LINE_YEAR_MISMATCH")
        item = session.get(BudgetItem, line.item_id)
        if not item.is_postable:
            raise ValidationFailed(f"البند {item.code} غير قابل للترحيل.", code="ITEM_NOT_POSTABLE")

    deltas = _deltas(req.entries)
    before = {lid: position_from_balance(fy, balances.get(lid)) for lid in line_ids}
    after = {lid: before[lid].with_deltas(deltas[lid]) for lid in line_ids}

    grant = _load_override(session, req)
    shortfalls, checks = [], []
    override_needed = ZERO
    for lid in line_ids:
        b, a = before[lid], after[lid]
        consumed = b.available - a.available
        if consumed > 0:
            chk = check_amount(b, consumed)
            checks.append({**line_label(session, lines[lid]), **chk.as_dict()})
            if not chk.ok:
                if grant is not None and grant.budget_line_id in (None, lid):
                    override_needed += chk.shortfall
                elif not req.historical_exception:
                    shortfalls.append({**line_label(session, lines[lid]), **chk.as_dict()})
        if b.unallocated is not None and a.unallocated is not None and a.unallocated < 0 \
                and a.unallocated < b.unallocated and not req.historical_exception:
            raise Conflict("مجموع التفويضات على البند يتجاوز اعتماده السنوي.", code="EXCEEDS_APPROPRIATION",
                           details={**line_label(session, lines[lid]), "appropriation": fmt(a.appropriation),
                                    "allocation_after": fmt(a.allocation)})
        if a.reservation < 0 or a.commitment < 0:
            raise Conflict("لا يمكن أن يصبح الحجز أو الارتباط القائم سالبًا.", code="NEGATIVE_OUTSTANDING")
    if shortfalls:
        first = shortfalls[0]
        raise InsufficientBudget(INSUFFICIENT_MESSAGE, details={
            **{k: (fmt(v) if isinstance(v, Decimal) else v) for k, v in first.items() if k != "message"},
            "lines": [{k: (fmt(v) if isinstance(v, Decimal) else v) for k, v in s.items() if k != "message"}
                      for s in shortfalls],
            "override_possible": False})
    if override_needed > 0:
        if grant.max_amount - grant.used_amount < override_needed:
            raise InsufficientBudget(INSUFFICIENT_MESSAGE, code="OVERRIDE_EXCEEDED", details={
                "shortfall": fmt(override_needed), "override_remaining": fmt(grant.max_amount - grant.used_amount)})
        grant.used_amount += override_needed

    created = []
    for e in req.entries:
        row = LedgerEntry(
            id=uuid.uuid4(), fiscal_year_id=fy.id, period_id=period.id, budget_line_id=e.budget_line_id,
            txn_type=e.txn_type, component=e.component, direction=e.direction, amount=e.amount,
            entry_date=req.entry_date, date_is_estimated=req.date_is_estimated, source_type=req.source_type,
            source_id=req.source_id, source_line_id=e.source_line_id, document_no=req.document_no,
            transfer_group_id=e.transfer_group_id, reversal_of_id=e.reversal_of_id,
            override_grant_id=grant.id if (grant is not None and override_needed > 0) else None,
            is_historical_exception=req.historical_exception, description=e.description,
            posted_by=req.posted_by, approved_by=req.approved_by)
        session.add(row)
        created.append(row)
    session.flush()
    for b in balances.values():
        session.refresh(b)
    return PostingResult(created, before, after, override_needed, checks)


# ---------------------------------------------------------------------------
# القيد العكسي
# ---------------------------------------------------------------------------
def reversal_specs(session: Session, source_type: str, source_id: uuid.UUID, reason: str) -> list[EntrySpec]:
    """قيود عكسية لكل قيود المستند غير المعكوسة. المناقلة تُعكس كاملة بمجموعة جديدة (FR-TR-03)."""
    originals = list(session.scalars(
        select(LedgerEntry).where(LedgerEntry.source_type == source_type, LedgerEntry.source_id == source_id,
                                  LedgerEntry.txn_type != "REVERSAL").order_by(LedgerEntry.entry_no)))
    reversed_ids = set(session.scalars(select(LedgerEntry.reversal_of_id).where(
        LedgerEntry.reversal_of_id.in_([o.id for o in originals]))))
    todo = [o for o in originals if o.id not in reversed_ids]
    if not todo:
        raise Conflict("لا توجد قيود قابلة للعكس لهذا المستند (ربما عُكس مسبقًا).", code="NOTHING_TO_REVERSE")
    groups: dict[uuid.UUID, uuid.UUID] = {}
    specs = []
    for o in todo:
        g = None
        if o.transfer_group_id:
            g = groups.setdefault(o.transfer_group_id, uuid.uuid4())
        specs.append(EntrySpec(budget_line_id=o.budget_line_id, txn_type="REVERSAL", component=o.component,
                               direction=-o.direction, amount=o.amount, source_line_id=o.source_line_id,
                               transfer_group_id=g, reversal_of_id=o.id, description=f"عكس: {reason}"))
    return specs


def reconcile(session: Session) -> list[uuid.UUID]:
    """INV-08: الأسطر التي تختلف أرصدتها المخزنة عن مجموع القيود (يجب أن تكون فارغة دائمًا)."""
    from sqlalchemy import text
    return list(session.scalars(text("SELECT budget_line_id FROM v_balance_reconciliation")))
