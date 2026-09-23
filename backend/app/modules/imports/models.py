import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("attachments.id"))
    fiscal_year_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fiscal_years.id"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"))
    status: Mapped[str] = mapped_column(String(12), default="UPLOADED")
    mapping: Mapped[dict | None] = mapped_column(JSONB)
    decisions: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    imported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id"))
    sheet_name: Mapped[str] = mapped_column(String(100))
    row_no: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSONB)
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    classification: Mapped[str | None] = mapped_column(String(40))
    excel_computed: Mapped[dict | None] = mapped_column(JSONB)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(12), default="PENDING")


class ImportIssue(Base):
    __tablename__ = "import_issues"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id"))
    row_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_rows.id"))
    severity: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(20))
    location: Mapped[str | None] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(20))
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
