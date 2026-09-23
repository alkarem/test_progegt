"""السنوات والفترات المالية (FR-FY)."""
import calendar
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalPeriod, FiscalYear

ADJUSTMENT_PERIOD = 13
DEFAULT_CARRY_FORWARD_CODE = "2/28"  # D-08


def get_year(session: Session, fiscal_year_id: uuid.UUID) -> FiscalYear:
    fy = session.get(FiscalYear, fiscal_year_id)
    if fy is None:
        raise NotFound("السنة المالية غير موجودة.")
    return fy


def get_year_by_number(session: Session, year: int) -> FiscalYear:
    fy = session.scalar(select(FiscalYear).where(FiscalYear.year == year))
    if fy is None:
        raise NotFound(f"السنة المالية {year} غير موجودة.")
    return fy


def _month_periods(start: date, end: date) -> list[tuple[date, date]]:
    periods, cur = [], start
    while cur <= end and len(periods) < 12:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        periods.append((cur, min(last, end)))
        cur = last + timedelta(days=1)
    return periods


def create_year(session: Session, *, year: int, start_date: date | None, end_date: date | None,
                control_basis: str, count_reservations: bool,
                carry_forward_item_id: uuid.UUID | None) -> FiscalYear:
    if session.scalar(select(FiscalYear.id).where(FiscalYear.year == year)):
        raise Conflict(f"السنة المالية {year} موجودة مسبقًا.", code="DUPLICATE_YEAR")
    start = start_date or date(year, 1, 1)
    end = end_date or date(year, 12, 31)
    if end <= start:
        raise ValidationFailed("تاريخ النهاية يجب أن يكون بعد تاريخ البداية.", code="INVALID_RANGE")
    months = _month_periods(start, end)
    if months[-1][1] != end:
        raise ValidationFailed("السنة المالية يجب ألا تتجاوز 12 شهرًا.", code="INVALID_RANGE")
    if carry_forward_item_id is None:
        carry_forward_item_id = session.scalar(select(BudgetItem.id).where(BudgetItem.code == DEFAULT_CARRY_FORWARD_CODE))
    elif session.get(BudgetItem, carry_forward_item_id) is None:
        raise ValidationFailed("بند ترحيل الارتباطات غير موجود.", code="UNKNOWN_ITEM")
    fy = FiscalYear(id=uuid.uuid4(), year=year, start_date=start, end_date=end, status="PLANNING",
                    control_basis=control_basis, count_reservations=count_reservations,
                    carry_forward_item_id=carry_forward_item_id)
    session.add(fy)
    session.flush()
    for n, (s, e) in enumerate(months, start=1):
        session.add(FiscalPeriod(fiscal_year_id=fy.id, period_no=n, start_date=s, end_date=e))
    # فترة التسويات (13) تغطي آخر يوم في السنة
    session.add(FiscalPeriod(fiscal_year_id=fy.id, period_no=ADJUSTMENT_PERIOD, start_date=end, end_date=end))
    session.flush()
    return fy


def open_year(session: Session, fy: FiscalYear) -> FiscalYear:
    if fy.status != "PLANNING":
        raise Conflict("يمكن فتح سنة في حالة التخطيط فقط.", code="INVALID_STATE")
    fy.status = "OPEN"
    session.flush()
    return fy


def update_year_settings(session: Session, fy: FiscalYear, changes: dict) -> FiscalYear:
    if fy.status in ("CLOSING", "CLOSED"):
        raise Conflict("لا يمكن تعديل إعدادات سنة قيد الإقفال أو مقفلة.", code="INVALID_STATE")
    if "control_basis" in changes and fy.status != "PLANNING" and changes["control_basis"] != fy.control_basis:
        raise Conflict("أساس الرقابة يُحدد قبل فتح السنة فقط.", code="CONTROL_BASIS_LOCKED")
    for k, v in changes.items():
        setattr(fy, k, v)
    session.flush()
    return fy


def list_periods(session: Session, fiscal_year_id: uuid.UUID) -> list[FiscalPeriod]:
    return list(session.scalars(select(FiscalPeriod).where(FiscalPeriod.fiscal_year_id == fiscal_year_id)
                                .order_by(FiscalPeriod.period_no)))


def get_period(session: Session, period_id: uuid.UUID) -> FiscalPeriod:
    p = session.get(FiscalPeriod, period_id)
    if p is None:
        raise NotFound("الفترة المالية غير موجودة.")
    return p


def period_for_date(session: Session, fy: FiscalYear, on: date, *, allow_adjustment: bool = False) -> FiscalPeriod:
    stmt = select(FiscalPeriod).where(FiscalPeriod.fiscal_year_id == fy.id, FiscalPeriod.start_date <= on,
                                      FiscalPeriod.end_date >= on)
    if not allow_adjustment:
        stmt = stmt.where(FiscalPeriod.period_no != ADJUSTMENT_PERIOD)
    p = session.scalar(stmt.order_by(FiscalPeriod.period_no))
    if p is None:
        raise ValidationFailed(f"التاريخ {on.isoformat()} خارج السنة المالية {fy.year}.", code="DATE_OUTSIDE_YEAR")
    return p


def close_period(session: Session, period: FiscalPeriod, user_id: uuid.UUID) -> FiscalPeriod:
    if period.status == "CLOSED":
        raise Conflict("الفترة مقفلة مسبقًا.", code="INVALID_STATE")
    period.status = "CLOSED"
    period.closed_by = user_id
    period.closed_at = datetime.now(UTC)
    session.flush()
    return period


def reopen_period(session: Session, period: FiscalPeriod) -> FiscalPeriod:
    fy = session.get(FiscalYear, period.fiscal_year_id)
    if fy.status == "CLOSED":
        raise Conflict("لا يمكن إعادة فتح فترة في سنة مقفلة.", code="YEAR_CLOSED")
    if period.status == "OPEN":
        raise Conflict("الفترة مفتوحة مسبقًا.", code="INVALID_STATE")
    period.status = "OPEN"
    period.closed_by = None
    period.closed_at = None
    session.flush()
    return period
