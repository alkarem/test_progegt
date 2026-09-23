import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.budget.models import DocumentMixin
from app.modules.ledger.models import Amount


class Adjustment(DocumentMixin, Base):
    __tablename__ = "adjustments"
    adjustment_no: Mapped[str] = mapped_column(String(50))
    adjustment_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(25))
    reverses_source_type: Mapped[str | None] = mapped_column(String(40))
    reverses_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commitments.id"))
    cancel_amount: Mapped[Decimal | None] = mapped_column(Amount)
    reason: Mapped[str] = mapped_column(Text)


class AdjustmentLine(Base):
    __tablename__ = "adjustment_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    adjustment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adjustments.id"))
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    component: Mapped[str] = mapped_column(String(15))
    direction: Mapped[int] = mapped_column(SmallInteger)
    amount: Mapped[Decimal] = mapped_column(Amount)
