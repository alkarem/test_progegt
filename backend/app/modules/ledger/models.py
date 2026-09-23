import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

Amount = Numeric(18, 3, asdecimal=True)


class BudgetLine(Base):
    """نقطة الرقابة: السنة × الجهة × البند."""
    __tablename__ = "budget_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fiscal_years.id"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_items.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetBalance(Base):
    """ذاكرة الأرصدة. للقراءة فقط من التطبيق؛ يحدّثها trigger دفتر الحركات."""
    __tablename__ = "budget_balances"
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"), primary_key=True)
    appropriation: Mapped[Decimal] = mapped_column(Amount)
    allocation: Mapped[Decimal] = mapped_column(Amount)
    transfer_in: Mapped[Decimal] = mapped_column(Amount)
    transfer_out: Mapped[Decimal] = mapped_column(Amount)
    reservation: Mapped[Decimal] = mapped_column(Amount)
    commitment: Mapped[Decimal] = mapped_column(Amount)
    actual: Mapped[Decimal] = mapped_column(Amount)
    version: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_no: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fiscal_years.id"))
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fiscal_periods.id"))
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    txn_type: Mapped[str] = mapped_column(String(30))
    component: Mapped[str] = mapped_column(String(15))
    direction: Mapped[int] = mapped_column(SmallInteger)
    amount: Mapped[Decimal] = mapped_column(Amount)
    entry_date: Mapped[date] = mapped_column(Date)
    date_is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_no: Mapped[str | None] = mapped_column(String(50))
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_entries.id"))
    override_grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("override_grants.id"))
    is_historical_exception: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    posted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverrideGrant(Base):
    __tablename__ = "override_grants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    budget_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("budget_lines.id"))
    max_amount: Mapped[Decimal] = mapped_column(Amount)
    used_amount: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
