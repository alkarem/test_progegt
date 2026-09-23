import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.budget import service
from app.modules.budget.models import BudgetDocument
from app.modules.catalog.models import BudgetItem
from app.modules.ledger.models import BudgetLine
from app.shared.money import Money, PositiveMoney
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(prefix="/budget-documents", tags=["الاعتماد الأصلي والتعديلات"])
KINDS = "^(ORIGINAL_BUDGET|BUDGET_INCREASE|BUDGET_DECREASE)$"


class LineIn(BaseModel):
    entity_id: uuid.UUID
    item_id: uuid.UUID
    amount: PositiveMoney


class DocIn(BaseModel):
    fiscal_year_id: uuid.UUID
    kind: str = Field(pattern=KINDS)
    doc_date: date
    description: str = Field(min_length=3, max_length=2000)
    reference: str | None = Field(default=None, max_length=100)
    doc_no: str | None = Field(default=None, max_length=50)
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class DocUpdate(BaseModel):
    doc_date: date | None = None
    description: str | None = Field(default=None, min_length=3, max_length=2000)
    reference: str | None = Field(default=None, max_length=100)
    lines: list[LineIn] | None = Field(default=None, min_length=1, max_length=500)


class LineOut(BaseModel):
    id: uuid.UUID
    budget_line_id: uuid.UUID
    entity_id: uuid.UUID
    item_id: uuid.UUID
    item_code: str
    item_name: str
    amount: Money


class DocOut(BaseModel):
    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    kind: str
    doc_no: str
    doc_date: date
    description: str
    reference: str | None
    status: str
    total: Money
    created_by: uuid.UUID
    created_at: datetime
    posted_at: datetime | None
    row_version: int
    lines: list[LineOut] = []


def doc_out(session: Session, d: BudgetDocument, with_lines: bool = True) -> DocOut:
    lines = []
    for ln in service.lines_of(session, d.id):
        bl = session.get(BudgetLine, ln.budget_line_id)
        item = session.get(BudgetItem, bl.item_id)
        lines.append(LineOut(id=ln.id, budget_line_id=bl.id, entity_id=bl.entity_id, item_id=item.id,
                             item_code=item.code, item_name=item.name, amount=ln.amount))
    return DocOut(id=d.id, fiscal_year_id=d.fiscal_year_id, kind=d.kind, doc_no=d.doc_no, doc_date=d.doc_date,
                  description=d.description, reference=d.reference, status=d.status,
                  total=sum((x.amount for x in lines), start=0), created_by=d.created_by, created_at=d.created_at,
                  posted_at=d.posted_at, row_version=d.row_version, lines=lines if with_lines else [])


@router.get("", response_model=Page[DocOut])
def list_docs(fiscal_year_id: uuid.UUID | None = None, status: str | None = None, kind: str | None = None,
              params: PageParams = Depends(), p: Principal = Depends(require("budget_documents.view")),
              session: Session = Depends(get_session)):
    stmt = select(BudgetDocument).order_by(BudgetDocument.doc_date.desc(), BudgetDocument.doc_no.desc())
    for col, value in ((BudgetDocument.fiscal_year_id, fiscal_year_id), (BudgetDocument.status, status),
                       (BudgetDocument.kind, kind)):
        if value is not None:
            stmt = stmt.where(col == value)
    if "FISCAL_YEAR" in p.scopes:
        stmt = stmt.where(BudgetDocument.fiscal_year_id.in_(p.scopes["FISCAL_YEAR"]))
    return paginate(session, stmt, params, lambda r: doc_out(session, r[0], with_lines=False))


@router.post("", response_model=DocOut, status_code=201)
def create_doc(body: DocIn, p: Principal = Depends(require("budget_documents.create")),
               meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    d = service.create_draft(session, p, **body.model_dump(exclude={"lines"}),
                             lines=[ln.model_dump() for ln in body.lines])
    session.commit()
    return doc_out(session, d)


@router.get("/{doc_id}", response_model=DocOut)
def get_doc(doc_id: uuid.UUID, p: Principal = Depends(require("budget_documents.view")),
            session: Session = Depends(get_session)):
    d = service.get(session, doc_id)
    p.require_scope("FISCAL_YEAR", d.fiscal_year_id)
    return doc_out(session, d)


@router.patch("/{doc_id}", response_model=DocOut)
def update_doc(doc_id: uuid.UUID, body: DocUpdate, if_match: int | None = Header(default=None),
               p: Principal = Depends(require("budget_documents.create")),
               meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    d = service.get(session, doc_id)
    changes = body.model_dump(exclude_unset=True)
    if "lines" in changes:
        changes["lines"] = [ln.model_dump() for ln in body.lines]
    service.update_draft(session, p, d, changes, if_match)
    session.commit()
    return doc_out(session, d)
