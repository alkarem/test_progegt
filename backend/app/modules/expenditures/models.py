import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.budget.models import DocumentMixin
from app.modules.ledger.models import Amount


class Expenditure(DocumentMixin, Base):
    __tablename__ = "expenditures"
    document_no: Mapped[str] = mapped_column(String(50))
    document_type: Mapped[str] = mapped_column(String(30), default="PAYMENT_VOUCHER")
    expenditure_date: Mapped[date] = mapped_column(Date)
    date_is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commitments.id"))
    expense_type: Mapped[str | None] = mapped_column(String(50))
    amount: Mapped[Decimal] = mapped_column(Amount)
    payment_method: Mapped[str] = mapped_column(String(20))
    payment_order_no: Mapped[str | None] = mapped_column(String(50))
    cheque_no: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
