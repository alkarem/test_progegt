import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.adjustments import service
from app.modules.adjustments.models import Adjustment
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(tags=["التسويات والقيود العكسية"])


class AdjLineIn(BaseModel):
    entity_id: uuid.UUID
    item_id: uuid.UUID
    component: str = Field(pattern="^(APPROPRIATION|ALLOCATION|ACTUAL)$")
    direction: int = Field(ge=-1, le=1)
    amount: PositiveMoney


class AdjustmentIn(BaseModel):
    fiscal_year_id: uuid.UUID
    kind: str = Field(pattern="^(ADJUSTMENT|COMMITMENT_CANCELLATION)$")
    adjustment_date: date
    reason: str = Field(min_length=5, max_length=2000)
    commitment_id: uuid.UUID | None = None
    cancel_amount: PositiveMoney | None = None
    lines: list[AdjLineIn] | None = Field(default=None, max_length=200)


class ReversalIn(BaseModel):
    source_type: str = Field(max_length=40)
    source_id: uuid.UUID
    adjustment_date: date
    reason: str = Field(min_length=5, max_length=2000)


class AdjustmentUpdate(BaseModel):
    adjustment_date: date | None = None
    reason: str | None = Field(default=None, min_length=5, max_length=2000)
    lines: list[AdjLineIn] | None = Field(default=None, max_length=200)


class AdjustmentOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    adjustment_no: str
    adjustment_date: date
    kind: str
    reverses_source_type: str | None
    reverses_source_id: uuid.UUID | None
    commitment_id: uuid.UUID | None
    amount: Money
    reason: str
    status: str
    created_by: uuid.UUID
    created_at: datetime
    posted_at: datetime | None
    row_version: int


def a_out(session: Session, a: Adjustment) -> AdjustmentOut:
    return AdjustmentOut(id=a.id, fiscal_year_id=a.fiscal_year_id, adjustment_no=a.adjustment_no,
                         adjustment_date=a.adjustment_date, kind=a.kind, reverses_source_type=a.reverses_source_type,
                         reverses_source_id=a.reverses_source_id, commitment_id=a.commitment_id,
                         amount=service.amount(session, a), reason=a.reason, status=a.status,
                         created_by=a.created_by, created_at=a.created_at, posted_at=a.posted_at,
                         row_version=a.row_version)


@router.get("/adjustments", response_model=Page[AdjustmentOut])
def list_adjustments(fiscal_year_id: uuid.UUID | None = None, kind: str | None = None, status: str | None = None,
                     params: PageParams = Depends(), p: Principal = Depends(require("adjustments.view")),
                     session: Session = Depends(get_session)):
    stmt = select(Adjustment).order_by(Adjustment.adjustment_date.desc(), Adjustment.adjustment_no.desc())
    for col, value in ((Adjustment.fiscal_year_id, fiscal_year_id), (Adjustment.kind, kind),
                       (Adjustment.status, status)):
        if value is not None:
            stmt = stmt.where(col == value)
    if "FISCAL_YEAR" in p.scopes:
        stmt = stmt.where(Adjustment.fiscal_year_id.in_(p.scopes["FISCAL_YEAR"]))
    return paginate(session, stmt, params, lambda r: a_out(session, r[0]))


@router.post("/adjustments", response_model=AdjustmentOut, status_code=201)
def create_adjustment(body: AdjustmentIn, p: Principal = Depends(require("adjustments.create")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason=body.reason)
    a = service.create(session, p, fiscal_year_id=body.fiscal_year_id, kind=body.kind,
                       adjustment_date=body.adjustment_date, reason=body.reason, commitment_id=body.commitment_id,
                       cancel_amount=body.cancel_amount,
                       lines=[ln.model_dump() for ln in body.lines] if body.lines else None)
    session.commit()
    return a_out(session, a)


@router.post("/reversals", response_model=AdjustmentOut, status_code=201)
def create_reversal(body: ReversalIn, p: Principal = Depends(require("adjustments.create")),
                    meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason=body.reason)
    from app.modules.workflow.registry import get_handler
    h = get_handler(body.source_type)
    p.require(f"{h.perm_prefix}.view")
    doc = h.get(session, body.source_id)
    a = service.create(session, p, fiscal_year_id=doc.fiscal_year_id, kind="REVERSAL",
                       adjustment_date=body.adjustment_date, reason=body.reason,
                       reverses_source_type=body.source_type, reverses_source_id=body.source_id)
    session.commit()
    return a_out(session, a)


@router.get("/adjustments/{adjustment_id}", response_model=AdjustmentOut)
def get_adjustment(adjustment_id: uuid.UUID, p: Principal = Depends(require("adjustments.view")),
                   session: Session = Depends(get_session)):
    a = service.get(session, adjustment_id)
    p.require_scope("FISCAL_YEAR", a.fiscal_year_id)
    return a_out(session, a)


@router.patch("/adjustments/{adjustment_id}", response_model=AdjustmentOut)
def update_adjustment(adjustment_id: uuid.UUID, body: AdjustmentUpdate, if_match: int | None = Header(default=None),
                      p: Principal = Depends(require("adjustments.create")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    a = service.get(session, adjustment_id)
    changes = body.model_dump(exclude_unset=True)
    if "lines" in changes:
        changes["lines"] = [ln.model_dump() for ln in body.lines]
    service.update_draft(session, p, a, changes, if_match)
    session.commit()
    return a_out(session, a)
