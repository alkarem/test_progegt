import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.budget.models import DocumentMixin
from app.modules.ledger.models import Amount


class Commitment(DocumentMixin, Base):
    __tablename__ = "commitments"
    commitment_no: Mapped[str] = mapped_column(String(50))
    commitment_type: Mapped[str] = mapped_column(String(20))
    commitment_status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    commitment_date: Mapped[date] = mapped_column(Date)
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("commitments.id"))
    amount: Mapped[Decimal] = mapped_column(Amount)
    description: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))
    expected_completion: Mapped[date | None] = mapped_column(Date)
    carried_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("commitments.id"))
