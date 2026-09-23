import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.shared.money import Money, PositiveMoney


class PositionOut(BaseModel):
    control_basis: str
    appropriation: Money
    allocation: Money
    transfer_in: Money
    transfer_out: Money
    reservation: Money
    commitment: Money
    actual: Money
    control_base: Money
    adjusted_budget: Money
    book_balance: Money
    available: Money
    unallocated: Money | None
    actual_rate: Decimal | None
    utilization_rate: Decimal | None


class LinePositionOut(BaseModel):
    budget_line_id: uuid.UUID | None
    fiscal_year: int
    entity_id: uuid.UUID
    item_id: uuid.UUID
    item_code: str
    item_name: str
    level: str
    position: PositionOut


class EntryOut(BaseModel):
    id: uuid.UUID
    entry_no: int
    entry_date: date
    date_is_estimated: bool
    budget_line_id: uuid.UUID
    item_code: str
    txn_type: str
    component: str
    direction: int
    amount: Money
    signed_amount: Money
    source_type: str
    source_id: uuid.UUID
    document_no: str | None
    description: str | None
    transfer_group_id: uuid.UUID | None
    reversal_of_id: uuid.UUID | None
    is_historical_exception: bool
    override_grant_id: uuid.UUID | None
    posted_by: uuid.UUID
    posted_at: datetime


class TimelineRow(EntryOut):
    available_after: Money


class CheckIn(BaseModel):
    amount: PositiveMoney


class CheckOut(BaseModel):
    ok: bool
    available: Money
    requested: Money
    shortfall: Money
    message: str | None


class OverrideGrantIn(BaseModel):
    user_id: uuid.UUID
    budget_line_id: uuid.UUID | None = None
    max_amount: PositiveMoney
    valid_from: datetime
    valid_to: datetime
    reason: str


class OverrideGrantOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    budget_line_id: uuid.UUID | None
    max_amount: Money
    used_amount: Money
    valid_from: datetime
    valid_to: datetime
    reason: str
    granted_by: uuid.UUID
    revoked_at: datetime | None
