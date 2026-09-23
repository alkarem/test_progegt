import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.authorizations import service
from app.modules.authorizations.models import Authorization
from app.modules.catalog.models import BudgetItem
from app.modules.ledger.models import BudgetLine
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(prefix="/authorizations", tags=["التفويضات"])


class AllocationIn(BaseModel):
    item_id: uuid.UUID
    amount: PositiveMoney


class AuthIn(BaseModel):
    fiscal_year_id: uuid.UUID
    auth_no: str = Field(min_length=1, max_length=50)
    auth_type: str = Field(pattern="^(FINANCIAL|DEPARTMENTAL|OTHER)$")
    auth_date: date
    entity_id: uuid.UUID
    period_from: date | None = None
    period_to: date | None = None
    amount: PositiveMoney
    purpose: str = Field(min_length=3, max_length=2000)
    funding_source_id: uuid.UUID | None = None
    over_allocation_reason: str | None = Field(default=None, max_length=2000)
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=500)


class AuthUpdate(BaseModel):
    auth_date: date | None = None
    period_from: date | None = None
    period_to: date | None = None
    amount: PositiveMoney | None = None
    purpose: str | None = Field(default=None, min_length=3, max_length=2000)
    funding_source_id: uuid.UUID | None = None
    over_allocation_reason: str | None = Field(default=None, max_length=2000)
    allocations: list[AllocationIn] | None = Field(default=None, max_length=500)


class AllocationOut(BaseModel):
    id: uuid.UUID
    budget_line_id: uuid.UUID
    item_id: uuid.UUID
    item_code: str
    item_name: str
    amount: Money


class AuthOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    auth_no: str
    auth_type: str
    auth_date: date
    entity_id: uuid.UUID
    period_from: date | None
    period_to: date | None
    amount: Money
    allocated: Money
    unallocated: Money
    purpose: str
    funding_source_id: uuid.UUID | None
    over_allocation_reason: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    posted_at: datetime | None
    row_version: int
    allocations: list[AllocationOut] = []


def auth_out(session: Session, a: Authorization, with_lines: bool = True) -> AuthOut:
    allocs = []
    for x in service.allocations_of(session, a.id):
        bl = session.get(BudgetLine, x.budget_line_id)
        item = session.get(BudgetItem, bl.item_id)
        allocs.append(AllocationOut(id=x.id, budget_line_id=bl.id, item_id=item.id, item_code=item.code,
                                    item_name=item.name, amount=x.amount))
    allocated = sum((x.amount for x in allocs), start=service.ZERO)
    return AuthOut(id=a.id, fiscal_year_id=a.fiscal_year_id, auth_no=a.auth_no, auth_type=a.auth_type,
                   auth_date=a.auth_date, entity_id=a.entity_id, period_from=a.period_from, period_to=a.period_to,
                   amount=a.amount, allocated=allocated, unallocated=a.amount - allocated, purpose=a.purpose,
                   funding_source_id=a.funding_source_id, over_allocation_reason=a.over_allocation_reason,
                   status=a.status, created_by=a.created_by, created_at=a.created_at, posted_at=a.posted_at,
                   row_version=a.row_version, allocations=allocs if with_lines else [])


@router.get("", response_model=Page[AuthOut])
def list_auths(fiscal_year_id: uuid.UUID | None = None, status: str | None = None, auth_no: str | None = None,
               params: PageParams = Depends(), p: Principal = Depends(require("authorizations.view")),
               session: Session = Depends(get_session)):
    stmt = select(Authorization).order_by(Authorization.auth_date.desc(), Authorization.auth_no)
    for col, value in ((Authorization.fiscal_year_id, fiscal_year_id), (Authorization.status, status),
                       (Authorization.auth_no, auth_no)):
        if value is not None:
            stmt = stmt.where(col == value)
    for scope, col in (("FISCAL_YEAR", Authorization.fiscal_year_id), ("ENTITY", Authorization.entity_id)):
        if scope in p.scopes:
            stmt = stmt.where(col.in_(p.scopes[scope]))
    return paginate(session, stmt, params, lambda r: auth_out(session, r[0], with_lines=False))


@router.post("", response_model=AuthOut, status_code=201)
def create_auth(body: AuthIn, p: Principal = Depends(require("authorizations.create")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    a = service.create_draft(session, p, **body.model_dump(exclude={"allocations"}),
                             allocations=[x.model_dump() for x in body.allocations])
    session.commit()
    return auth_out(session, a)


@router.get("/{auth_id}", response_model=AuthOut)
def get_auth(auth_id: uuid.UUID, p: Principal = Depends(require("authorizations.view")),
             session: Session = Depends(get_session)):
    a = service.get(session, auth_id)
    p.require_scope("FISCAL_YEAR", a.fiscal_year_id)
    p.require_scope("ENTITY", a.entity_id)
    return auth_out(session, a)


@router.patch("/{auth_id}", response_model=AuthOut)
def update_auth(auth_id: uuid.UUID, body: AuthUpdate, if_match: int | None = Header(default=None),
                p: Principal = Depends(require("authorizations.create")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    a = service.get(session, auth_id)
    changes = body.model_dump(exclude_unset=True)
    if "allocations" in changes:
        changes["allocations"] = [x.model_dump() for x in body.allocations]
    service.update_draft(session, p, a, changes, if_match)
    session.commit()
    return auth_out(session, a)
