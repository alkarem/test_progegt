import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.budget.models import DocumentMixin
from app.modules.ledger.models import Amount


class Authorization(DocumentMixin, Base):
    __tablename__ = "authorizations"
    auth_no: Mapped[str] = mapped_column(String(50))
    auth_type: Mapped[str] = mapped_column(String(15))
    auth_date: Mapped[date] = mapped_column(Date)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Amount)
    purpose: Mapped[str] = mapped_column(Text)
    funding_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("funding_sources.id"))
    over_allocation_reason: Mapped[str | None] = mapped_column(Text)
    over_allocation_approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AuthorizationAllocation(Base):
    __tablename__ = "authorization_allocations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authorization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authorizations.id"))
    budget_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budget_lines.id"))
    amount: Mapped[Decimal] = mapped_column(Amount)
