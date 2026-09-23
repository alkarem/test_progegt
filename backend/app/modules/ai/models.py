import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AIInteraction(Base):
    """سجل تفاعلات المساعد (12-ai §6): للإضافة فقط (trigger يمنع التعديل والحذف)."""
    __tablename__ = "ai_interactions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    fiscal_year_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fiscal_years.id"))
    kind: Mapped[str] = mapped_column(String(10))
    question: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    grounding: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(60))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
