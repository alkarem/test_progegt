import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.ledger.models import Amount


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    source_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    separate_approvers: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_definitions.id"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))
    min_amount: Mapped[Decimal | None] = mapped_column(Amount)
    max_amount: Mapped[Decimal | None] = mapped_column(Amount)
    runs_budget_check: Mapped[bool] = mapped_column(Boolean, default=False)
    posts: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_days: Mapped[int] = mapped_column(SmallInteger, default=3)


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_definitions.id"))
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fiscal_years.id"))
    current_step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_steps.id"))
    state: Mapped[str] = mapped_column(String(12))
    round: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[Decimal] = mapped_column(Amount)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    step_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowAction(Base):
    __tablename__ = "workflow_actions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_instances.id"))
    round: Mapped[int] = mapped_column(Integer)
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_steps.id"))
    action: Mapped[str] = mapped_column(String(12))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    budget_check: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Delegation(Base):
    __tablename__ = "delegations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delegator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    delegate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
