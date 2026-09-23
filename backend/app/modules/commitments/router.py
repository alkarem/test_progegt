import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.catalog.models import BudgetItem, Supplier
from app.modules.commitments import service
from app.modules.commitments.models import Commitment
from app.modules.ledger.models import BudgetLine, LedgerEntry
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(prefix="/commitments", tags=["الارتباطات"])
TYPES = "^(PURCHASE_REQUEST|PURCHASE_ORDER|CONTRACT|OBLIGATION|OTHER)$"


class CommitmentIn(BaseModel):
    fiscal_year_id: uuid.UUID
    commitment_type: str = Field(pattern=TYPES)
    commitment_date: date
    entity_id: uuid.UUID
    item_id: uuid.UUID
    amount: PositiveMoney
    description: str = Field(min_length=3, max_length=2000)
    supplier_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)
    expected_completion: date | None = None
    commitment_no: str | None = Field(default=None, max_length=50)


class CommitmentUpdate(BaseModel):
    commitment_date: date | None = None
    amount: PositiveMoney | None = None
    description: str | None = Field(default=None, min_length=3, max_length=2000)
    supplier_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)
    expected_completion: date | None = None


class ConvertIn(BaseModel):
    commitment_type: str = Field(pattern="^(PURCHASE_ORDER|CONTRACT|OBLIGATION|OTHER)$")
    commitment_date: date
    amount: PositiveMoney
    description: str = Field(min_length=3, max_length=2000)
    supplier_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)


class CommitmentOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    commitment_no: str
    commitment_type: str
    commitment_status: str
    commitment_date: date
    budget_line_id: uuid.UUID
    item_code: str
    item_name: str
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    parent_id: uuid.UUID | None
    amount: Money
    paid: Money
    cancelled: Money
    outstanding: Money
    description: str
    reference: str | None
    expected_completion: date | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    posted_at: datetime | None
    row_version: int


class MovementOut(BaseModel):
    entry_no: int
    entry_date: date
    txn_type: str
    signed_amount: Money
    source_type: str
    source_id: uuid.UUID
    document_no: str | None


def c_out(session: Session, c: Commitment) -> CommitmentOut:
    bl = session.get(BudgetLine, c.budget_line_id)
    item = session.get(BudgetItem, bl.item_id)
    sup = session.get(Supplier, c.supplier_id) if c.supplier_id else None
    posted = c.status == "POSTED"
    return CommitmentOut(
        id=c.id, fiscal_year_id=c.fiscal_year_id, commitment_no=c.commitment_no, commitment_type=c.commitment_type,
        commitment_status=c.commitment_status, commitment_date=c.commitment_date, budget_line_id=c.budget_line_id,
        item_code=item.code, item_name=item.name, supplier_id=c.supplier_id, supplier_name=sup.name if sup else None,
        parent_id=c.parent_id, amount=c.amount, paid=service.paid(session, c) if posted else 0,
        cancelled=service.cancelled(session, c) if posted else 0,
        outstanding=service.outstanding(session, c) if posted else 0, description=c.description,
        reference=c.reference, expected_completion=c.expected_completion, status=c.status, created_by=c.created_by,
        created_at=c.created_at, posted_at=c.posted_at, row_version=c.row_version)


@router.get("", response_model=Page[CommitmentOut])
def list_commitments(fiscal_year_id: uuid.UUID | None = None, status: str | None = None,
                     commitment_status: str | None = None, commitment_type: str | None = None,
                     supplier_id: uuid.UUID | None = None, budget_line_id: uuid.UUID | None = None,
                     params: PageParams = Depends(), p: Principal = Depends(require("commitments.view")),
                     session: Session = Depends(get_session)):
    stmt = select(Commitment).join(BudgetLine, BudgetLine.id == Commitment.budget_line_id).order_by(
        Commitment.commitment_date.desc(), Commitment.commitment_no.desc())
    for col, value in ((Commitment.fiscal_year_id, fiscal_year_id), (Commitment.status, status),
                       (Commitment.commitment_status, commitment_status), (Commitment.commitment_type, commitment_type),
                       (Commitment.supplier_id, supplier_id), (Commitment.budget_line_id, budget_line_id)):
        if value is not None:
            stmt = stmt.where(col == value)
    for scope, col in (("FISCAL_YEAR", Commitment.fiscal_year_id), ("ENTITY", BudgetLine.entity_id),
                       ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return paginate(session, stmt, params, lambda r: c_out(session, r[0]))


@router.post("", response_model=CommitmentOut, status_code=201)
def create_commitment(body: CommitmentIn, p: Principal = Depends(require("commitments.create")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    c = service.create_draft(session, p, **body.model_dump())
    session.commit()
    return c_out(session, c)


def _scoped(session: Session, p: Principal, cid: uuid.UUID) -> Commitment:
    c = service.get(session, cid)
    p.require_scope("FISCAL_YEAR", c.fiscal_year_id)
    bl = session.get(BudgetLine, c.budget_line_id)
    p.require_scope("ENTITY", bl.entity_id)
    p.require_scope("ITEM", bl.item_id)
    return c


@router.get("/{commitment_id}", response_model=CommitmentOut)
def get_commitment(commitment_id: uuid.UUID, p: Principal = Depends(require("commitments.view")),
                   session: Session = Depends(get_session)):
    return c_out(session, _scoped(session, p, commitment_id))


@router.patch("/{commitment_id}", response_model=CommitmentOut)
def update_commitment(commitment_id: uuid.UUID, body: CommitmentUpdate, if_match: int | None = Header(default=None),
                      p: Principal = Depends(require("commitments.create")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    c = _scoped(session, p, commitment_id)
    service.update_draft(session, p, c, body.model_dump(exclude_unset=True), if_match)
    session.commit()
    return c_out(session, c)


@router.post("/{commitment_id}/convert", response_model=CommitmentOut, status_code=201)
def convert(commitment_id: uuid.UUID, body: ConvertIn, p: Principal = Depends(require("commitments.create")),
            meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    pr = _scoped(session, p, commitment_id)
    c = service.convert(session, p, pr, **body.model_dump())
    session.commit()
    return c_out(session, c)


@router.get("/{commitment_id}/movements", response_model=list[MovementOut])
def movements(commitment_id: uuid.UUID, p: Principal = Depends(require("commitments.view")),
              session: Session = Depends(get_session)):
    c = _scoped(session, p, commitment_id)
    rows = session.scalars(select(LedgerEntry).where(LedgerEntry.commitment_id == c.id).order_by(LedgerEntry.entry_no))
    return [MovementOut(entry_no=e.entry_no, entry_date=e.entry_date, txn_type=e.txn_type,
                        signed_amount=e.direction * e.amount, source_type=e.source_type, source_id=e.source_id,
                        document_no=e.document_no) for e in rows]
