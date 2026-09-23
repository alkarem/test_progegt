import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.fiscal import service
from app.modules.fiscal.models import FiscalYear

router = APIRouter(tags=["السنوات المالية"])


class YearIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    control_basis: str = Field(default="TWO_LEVEL", pattern="^(APPROPRIATION|AUTHORIZATION|TWO_LEVEL)$")
    count_reservations: bool = True
    carry_forward_item_id: uuid.UUID | None = None


class YearUpdate(BaseModel):
    control_basis: str | None = Field(default=None, pattern="^(APPROPRIATION|AUTHORIZATION|TWO_LEVEL)$")
    count_reservations: bool | None = None
    carry_forward_item_id: uuid.UUID | None = None


class YearOut(BaseModel):
    id: uuid.UUID
    year: int
    start_date: date
    end_date: date
    status: str
    control_basis: str
    count_reservations: bool
    carry_forward_item_id: uuid.UUID | None


class PeriodOut(BaseModel):
    id: uuid.UUID
    period_no: int
    start_date: date
    end_date: date
    status: str
    closed_at: datetime | None


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def _y(fy: FiscalYear) -> YearOut:
    return YearOut.model_validate(fy, from_attributes=True)


@router.get("/fiscal-years", response_model=list[YearOut])
def list_years(p: Principal = Depends(require("fiscal.view")), session: Session = Depends(get_session)):
    stmt = select(FiscalYear).order_by(FiscalYear.year.desc())
    if "FISCAL_YEAR" in p.scopes:
        stmt = stmt.where(FiscalYear.id.in_(p.scopes["FISCAL_YEAR"]))
    return [_y(fy) for fy in session.scalars(stmt)]


@router.post("/fiscal-years", response_model=YearOut, status_code=201)
def create_year(body: YearIn, p: Principal = Depends(require("fiscal.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    fy = service.create_year(session, **body.model_dump())
    session.commit()
    return _y(fy)


@router.get("/fiscal-years/{fy_id}", response_model=YearOut)
def get_year(fy_id: uuid.UUID, p: Principal = Depends(require("fiscal.view")), session: Session = Depends(get_session)):
    p.require_scope("FISCAL_YEAR", fy_id)
    return _y(service.get_year(session, fy_id))


@router.patch("/fiscal-years/{fy_id}", response_model=YearOut)
def update_year(fy_id: uuid.UUID, body: YearUpdate, p: Principal = Depends(require("fiscal.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    fy = service.update_year_settings(session, service.get_year(session, fy_id), body.model_dump(exclude_unset=True))
    session.commit()
    return _y(fy)


@router.post("/fiscal-years/{fy_id}/open", response_model=YearOut)
def open_year(fy_id: uuid.UUID, p: Principal = Depends(require("fiscal.manage")),
              meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    fy = service.open_year(session, service.get_year(session, fy_id))
    session.commit()
    return _y(fy)


@router.get("/fiscal-years/{fy_id}/periods", response_model=list[PeriodOut])
def periods(fy_id: uuid.UUID, p: Principal = Depends(require("fiscal.view")), session: Session = Depends(get_session)):
    p.require_scope("FISCAL_YEAR", fy_id)
    return [PeriodOut.model_validate(x, from_attributes=True) for x in service.list_periods(session, fy_id)]


@router.post("/periods/{period_id}/close", response_model=PeriodOut)
def close_period(period_id: uuid.UUID, p: Principal = Depends(require("fiscal.close_period")),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    per = service.close_period(session, service.get_period(session, period_id), p.user_id)
    session.commit()
    return PeriodOut.model_validate(per, from_attributes=True)


@router.post("/periods/{period_id}/reopen", response_model=PeriodOut)
def reopen_period(period_id: uuid.UUID, body: ReasonIn, p: Principal = Depends(require("fiscal.close_period")),
                  meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason=body.reason)
    per = service.reopen_period(session, service.get_period(session, period_id))
    session.commit()
    return PeriodOut.model_validate(per, from_attributes=True)
