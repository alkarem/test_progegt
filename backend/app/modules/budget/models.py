import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.ledger.models import Amount


class DocumentMixin:
    """الأعمدة المشتركة لكل المستندات المالية (04-database §3.2)."""
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fiscal_years.id"))
    status: Mapped[str] = mapped_column(String(12), default="DRAFT")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    legacy_ref: Mapped[str | None] = mapped_column(String(100))
    is_historical_exception: Mapped[bool] = mapped_column(Boolean, default=False)


class BudgetDocument(DocumentMixin, Base):
    __tablename__ = "budget_documents"
    kind: Mapped[str] = mapped_column(String(20))
    doc_no: Mapped[str] = mapped_column(String(50))
    doc_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)


class BudgetDocumentLine(Base):
    __tablename__ = "budget_document_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_documents.id"))
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    amount: Mapped[Decimal] = mapped_column(Amount)
