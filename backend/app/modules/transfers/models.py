import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.budget.models import DocumentMixin
from app.modules.ledger.models import Amount


class Transfer(DocumentMixin, Base):
    __tablename__ = "transfers"
    transfer_no: Mapped[str] = mapped_column(String(50))
    transfer_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    approval_no: Mapped[str | None] = mapped_column(String(50))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class TransferLine(Base):
    __tablename__ = "transfer_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transfer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transfers.id"))
    from_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    to_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    amount: Mapped[Decimal] = mapped_column(Amount)
