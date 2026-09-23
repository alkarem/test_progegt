import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.catalog.models import BudgetItem
from app.modules.ledger import engine
from app.modules.ledger.models import BudgetLine
from app.modules.transfers import service
from app.modules.transfers.models import Transfer
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(prefix="/transfers", tags=["المناقلات"])


class TransferLineIn(BaseModel):
    from_item_id: uuid.UUID
    to_item_id: uuid.UUID
    amount: PositiveMoney
    from_entity_id: uuid.UUID | None = None
    to_entity_id: uuid.UUID | None = None


class TransferIn(BaseModel):
    fiscal_year_id: uuid.UUID
    entity_id: uuid.UUID
    transfer_date: date
    reason: str = Field(min_length=3, max_length=2000)
    approval_no: str | None = Field(default=None, max_length=50)
    transfer_no: str | None = Field(default=None, max_length=50)
    lines: list[TransferLineIn] = Field(min_length=1, max_length=200)


class TransferUpdate(BaseModel):
    entity_id: uuid.UUID | None = None
    transfer_date: date | None = None
    reason: str | None = Field(default=None, min_length=3, max_length=2000)
    approval_no: str | None = Field(default=None, max_length=50)
    lines: list[TransferLineIn] | None = Field(default=None, min_length=1, max_length=200)


class SideOut(BaseModel):
    budget_line_id: uuid.UUID
    item_code: str
    item_name: str
    available_now: Money


class TransferLineOut(BaseModel):
    id: uuid.UUID
    source: SideOut
    target: SideOut
    amount: Money


class TransferOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    transfer_no: str
    transfer_date: date
    reason: str
    approval_no: str | None
    status: str
    total: Money
    created_by: uuid.UUID
    approved_by: uuid.UUID | None
    created_at: datetime
    posted_at: datetime | None
    row_version: int
    lines: list[TransferLineOut] = []


def _side(session: Session, line_id: uuid.UUID) -> SideOut:
    bl = session.get(BudgetLine, line_id)
    item = session.get(BudgetItem, bl.item_id)
    return SideOut(budget_line_id=bl.id, item_code=item.code, item_name=item.name,
                   available_now=engine.current_position(session, bl).available)


def transfer_out(session: Session, t: Transfer, with_lines: bool = True) -> TransferOut:
    lines = [TransferLineOut(id=ln.id, source=_side(session, ln.from_line_id), target=_side(session, ln.to_line_id),
                             amount=ln.amount) for ln in service.lines_of(session, t.id)] if with_lines else []
    return TransferOut(id=t.id, fiscal_year_id=t.fiscal_year_id, transfer_no=t.transfer_no,
                       transfer_date=t.transfer_date, reason=t.reason, approval_no=t.approval_no, status=t.status,
                       total=service.total(session, t), created_by=t.created_by, approved_by=t.approved_by,
                       created_at=t.created_at, posted_at=t.posted_at, row_version=t.row_version, lines=lines)


def _lines(entity_id: uuid.UUID, lines: list[TransferLineIn]) -> list[dict]:
    return [{"from_entity_id": ln.from_entity_id or entity_id, "from_item_id": ln.from_item_id,
             "to_entity_id": ln.to_entity_id or entity_id, "to_item_id": ln.to_item_id, "amount": ln.amount}
            for ln in lines]


@router.get("", response_model=Page[TransferOut])
def list_transfers(fiscal_year_id: uuid.UUID | None = None, status: str | None = None,
                   params: PageParams = Depends(), p: Principal = Depends(require("transfers.view")),
                   session: Session = Depends(get_session)):
    stmt = select(Transfer).order_by(Transfer.transfer_date.desc(), Transfer.transfer_no.desc())
    for col, value in ((Transfer.fiscal_year_id, fiscal_year_id), (Transfer.status, status)):
        if value is not None:
            stmt = stmt.where(col == value)
    if "FISCAL_YEAR" in p.scopes:
        stmt = stmt.where(Transfer.fiscal_year_id.in_(p.scopes["FISCAL_YEAR"]))
    return paginate(session, stmt, params, lambda r: transfer_out(session, r[0], with_lines=False))


@router.post("", response_model=TransferOut, status_code=201)
def create_transfer(body: TransferIn, p: Principal = Depends(require("transfers.create")),
                    meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    t = service.create_draft(session, p, fiscal_year_id=body.fiscal_year_id, transfer_date=body.transfer_date,
                             reason=body.reason, approval_no=body.approval_no, transfer_no=body.transfer_no,
                             lines=_lines(body.entity_id, body.lines))
    session.commit()
    return transfer_out(session, t)


@router.get("/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: uuid.UUID, p: Principal = Depends(require("transfers.view")),
                 session: Session = Depends(get_session)):
    t = service.get(session, transfer_id)
    p.require_scope("FISCAL_YEAR", t.fiscal_year_id)
    return transfer_out(session, t)


@router.patch("/{transfer_id}", response_model=TransferOut)
def update_transfer(transfer_id: uuid.UUID, body: TransferUpdate, if_match: int | None = Header(default=None),
                    p: Principal = Depends(require("transfers.create")),
                    meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    t = service.get(session, transfer_id)
    changes = body.model_dump(exclude_unset=True, exclude={"entity_id", "lines"})
    if body.lines is not None:
        if body.entity_id is None:
            from app.core.errors import ValidationFailed
            raise ValidationFailed("حدد الجهة مع أسطر المناقلة.", code="ENTITY_REQUIRED")
        changes["lines"] = _lines(body.entity_id, body.lines)
    service.update_draft(session, p, t, changes, if_match)
    session.commit()
    return transfer_out(session, t)
