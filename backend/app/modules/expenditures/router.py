import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.catalog.models import BudgetItem, Supplier
from app.modules.expenditures import service
from app.modules.expenditures.models import Expenditure
from app.modules.ledger.models import BudgetLine
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(prefix="/expenditures", tags=["المصروفات"])
METHODS = "^(CHEQUE|BANK_TRANSFER|CASH|DEPOSIT_ACCOUNT|OTHER)$"


class ExpenditureIn(BaseModel):
    fiscal_year_id: uuid.UUID
    expenditure_date: date
    entity_id: uuid.UUID
    item_id: uuid.UUID
    amount: PositiveMoney
    payment_method: str = Field(pattern=METHODS)
    description: str = Field(min_length=3, max_length=2000)
    document_no: str | None = Field(default=None, max_length=50)
    document_type: str = Field(default="PAYMENT_VOUCHER", max_length=30)
    supplier_id: uuid.UUID | None = None
    commitment_id: uuid.UUID | None = None
    expense_type: str | None = Field(default=None, max_length=50)
    payment_order_no: str | None = Field(default=None, max_length=50)
    cheque_no: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)


class ExpenditureUpdate(BaseModel):
    expenditure_date: date | None = None
    amount: PositiveMoney | None = None
    payment_method: str | None = Field(default=None, pattern=METHODS)
    description: str | None = Field(default=None, min_length=3, max_length=2000)
    document_no: str | None = Field(default=None, max_length=50)
    supplier_id: uuid.UUID | None = None
    commitment_id: uuid.UUID | None = None
    expense_type: str | None = Field(default=None, max_length=50)
    payment_order_no: str | None = Field(default=None, max_length=50)
    cheque_no: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)


class ExpenditureOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    document_no: str
    document_type: str
    expenditure_date: date
    date_is_estimated: bool
    budget_line_id: uuid.UUID
    item_code: str
    item_name: str
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    commitment_id: uuid.UUID | None
    expense_type: str | None
    amount: Money
    payment_method: str
    payment_order_no: str | None
    cheque_no: str | None
    description: str
    notes: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    posted_at: datetime | None
    row_version: int
    possible_duplicates: list[str] = []


def e_out(session: Session, e: Expenditure, with_dups: bool = False) -> ExpenditureOut:
    bl = session.get(BudgetLine, e.budget_line_id)
    item = session.get(BudgetItem, bl.item_id)
    sup = session.get(Supplier, e.supplier_id) if e.supplier_id else None
    dups = [x.document_no for x in service.similar(session, e)] if with_dups else []
    return ExpenditureOut(id=e.id, fiscal_year_id=e.fiscal_year_id, document_no=e.document_no,
                          document_type=e.document_type, expenditure_date=e.expenditure_date,
                          date_is_estimated=e.date_is_estimated, budget_line_id=bl.id, item_code=item.code,
                          item_name=item.name, supplier_id=e.supplier_id, supplier_name=sup.name if sup else None,
                          commitment_id=e.commitment_id, expense_type=e.expense_type, amount=e.amount,
                          payment_method=e.payment_method, payment_order_no=e.payment_order_no,
                          cheque_no=e.cheque_no, description=e.description, notes=e.notes, status=e.status,
                          created_by=e.created_by, created_at=e.created_at, posted_at=e.posted_at,
                          row_version=e.row_version, possible_duplicates=dups)


@router.get("", response_model=Page[ExpenditureOut])
def list_expenditures(fiscal_year_id: uuid.UUID | None = None, status: str | None = None,
                      supplier_id: uuid.UUID | None = None, commitment_id: uuid.UUID | None = None,
                      budget_line_id: uuid.UUID | None = None, date_from: date | None = None,
                      date_to: date | None = None, params: PageParams = Depends(),
                      p: Principal = Depends(require("expenditures.view")), session: Session = Depends(get_session)):
    stmt = select(Expenditure).join(BudgetLine, BudgetLine.id == Expenditure.budget_line_id).order_by(
        Expenditure.expenditure_date.desc(), Expenditure.document_no.desc())
    for col, value in ((Expenditure.fiscal_year_id, fiscal_year_id), (Expenditure.status, status),
                       (Expenditure.supplier_id, supplier_id), (Expenditure.commitment_id, commitment_id),
                       (Expenditure.budget_line_id, budget_line_id)):
        if value is not None:
            stmt = stmt.where(col == value)
    if date_from:
        stmt = stmt.where(Expenditure.expenditure_date >= date_from)
    if date_to:
        stmt = stmt.where(Expenditure.expenditure_date <= date_to)
    for scope, col in (("FISCAL_YEAR", Expenditure.fiscal_year_id), ("ENTITY", BudgetLine.entity_id),
                       ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return paginate(session, stmt, params, lambda r: e_out(session, r[0]))


@router.post("", response_model=ExpenditureOut, status_code=201)
def create_expenditure(body: ExpenditureIn, p: Principal = Depends(require("expenditures.create")),
                       meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    e = service.create_draft(session, p, **body.model_dump())
    session.commit()
    return e_out(session, e, with_dups=True)


def _scoped(session: Session, p: Principal, eid: uuid.UUID) -> Expenditure:
    e = service.get(session, eid)
    bl = session.get(BudgetLine, e.budget_line_id)
    p.require_scope("FISCAL_YEAR", e.fiscal_year_id)
    p.require_scope("ENTITY", bl.entity_id)
    p.require_scope("ITEM", bl.item_id)
    return e


@router.get("/{expenditure_id}", response_model=ExpenditureOut)
def get_expenditure(expenditure_id: uuid.UUID, p: Principal = Depends(require("expenditures.view")),
                    session: Session = Depends(get_session)):
    return e_out(session, _scoped(session, p, expenditure_id), with_dups=True)


@router.patch("/{expenditure_id}", response_model=ExpenditureOut)
def update_expenditure(expenditure_id: uuid.UUID, body: ExpenditureUpdate, if_match: int | None = Header(default=None),
                       p: Principal = Depends(require("expenditures.create")),
                       meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    e = _scoped(session, p, expenditure_id)
    service.update_draft(session, p, e, body.model_dump(exclude_unset=True), if_match)
    session.commit()
    return e_out(session, e, with_dups=True)
