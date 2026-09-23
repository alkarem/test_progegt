import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.catalog import service
from app.modules.catalog.models import BudgetChapter, BudgetItem, Entity, FundingSource, Supplier
from app.shared.arabic import normalize_party_name
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(tags=["البيانات المرجعية"])


class ChapterIn(BaseModel):
    code: str = Field(pattern=r"^[0-9]{1,3}$")
    name: str = Field(min_length=2, max_length=200)
    display_order: int = 0


class ChapterOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    display_order: int


class ItemIn(BaseModel):
    chapter_id: uuid.UUID
    code: str = Field(pattern=r"^\s*[0-9]+(\s*/\s*[0-9]+)+\s*$", max_length=20)
    name: str = Field(min_length=2, max_length=200)
    parent_id: uuid.UUID | None = None
    budget_type: str = Field(default="EXPENSE", max_length=30)
    display_order: int = 0


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    parent_id: uuid.UUID | None = None
    budget_type: str | None = Field(default=None, max_length=30)
    display_order: int | None = None
    is_active: bool | None = None
    is_postable: bool | None = None


class ItemOut(BaseModel):
    id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_code: str
    code: str
    name: str
    parent_id: uuid.UUID | None
    budget_type: str
    is_postable: bool
    is_active: bool
    display_order: int


class ItemNode(ItemOut):
    children: list["ItemNode"] = []


class EntityIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=2, max_length=200)
    parent_id: uuid.UUID | None = None


class EntityOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    parent_id: uuid.UUID | None
    is_active: bool


class FundingIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=2, max_length=200)


class FundingOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=250)
    kind: str = Field(default="COMPANY", pattern="^(COMPANY|PERSON|GOV_ACCOUNT|OTHER)$")
    tax_no: str | None = Field(default=None, max_length=50)
    commercial_reg: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=2000)
    allow_similar: bool = False


class SupplierOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    tax_no: str | None
    commercial_reg: str | None
    phone: str | None
    is_active: bool


def _item_out(item: BudgetItem, chapter_code: str) -> ItemOut:
    return ItemOut(id=item.id, chapter_id=item.chapter_id, chapter_code=chapter_code, code=item.code,
                   name=item.name, parent_id=item.parent_id, budget_type=item.budget_type,
                   is_postable=item.is_postable, is_active=item.is_active, display_order=item.display_order)


# --- الأبواب -----------------------------------------------------------------
@router.get("/chapters", response_model=list[ChapterOut])
def list_chapters(_: Principal = Depends(require("catalog.view")), session: Session = Depends(get_session)):
    return [ChapterOut.model_validate(c, from_attributes=True)
            for c in session.scalars(select(BudgetChapter).order_by(BudgetChapter.display_order))]


@router.post("/chapters", response_model=ChapterOut, status_code=201)
def create_chapter(body: ChapterIn, p: Principal = Depends(require("catalog.manage")),
                   meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    c = service.create_chapter(session, body.code, body.name, body.display_order)
    session.commit()
    return ChapterOut.model_validate(c, from_attributes=True)


# --- البنود ------------------------------------------------------------------
@router.get("/items", response_model=list[ItemOut])
def list_items(chapter_id: uuid.UUID | None = None, active_only: bool = False,
               p: Principal = Depends(require("catalog.view")), session: Session = Depends(get_session)):
    return [_item_out(i, code) for i, code in session.execute(service.list_items(session, p, chapter_id, active_only))]


@router.get("/items/tree", response_model=list[ItemNode])
def items_tree(chapter_id: uuid.UUID | None = None, p: Principal = Depends(require("catalog.view")),
               session: Session = Depends(get_session)):
    nodes = {i.id: ItemNode(**_item_out(i, code).model_dump())
             for i, code in session.execute(service.list_items(session, p, chapter_id, False))}
    roots = []
    for n in nodes.values():
        (nodes[n.parent_id].children if n.parent_id in nodes else roots).append(n)
    return roots


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemIn, p: Principal = Depends(require("catalog.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    item = service.create_item(session, chapter_id=body.chapter_id, code=body.code, name=body.name,
                               parent_id=body.parent_id, budget_type=body.budget_type,
                               display_order=body.display_order)
    session.commit()
    return _item_out(item, service.get_chapter(session, item.chapter_id).code)


@router.patch("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: uuid.UUID, body: ItemUpdate, p: Principal = Depends(require("catalog.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    item = service.update_item(session, service.get_item(session, item_id), body.model_dump(exclude_unset=True))
    session.commit()
    return _item_out(item, service.get_chapter(session, item.chapter_id).code)


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: uuid.UUID, p: Principal = Depends(require("catalog.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    service.delete_item(session, service.get_item(session, item_id))
    session.commit()


# --- الجهات ومصادر التمويل ---------------------------------------------------
@router.get("/entities", response_model=list[EntityOut])
def list_entities(p: Principal = Depends(require("catalog.view")), session: Session = Depends(get_session)):
    stmt = select(Entity).order_by(Entity.code)
    if "ENTITY" in p.scopes:
        stmt = stmt.where(Entity.id.in_(p.scopes["ENTITY"]))
    return [EntityOut.model_validate(e, from_attributes=True) for e in session.scalars(stmt)]


@router.post("/entities", response_model=EntityOut, status_code=201)
def create_entity(body: EntityIn, p: Principal = Depends(require("catalog.manage")),
                  meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    e = service.create_entity(session, body.code, body.name, body.parent_id)
    session.commit()
    return EntityOut.model_validate(e, from_attributes=True)


@router.get("/funding-sources", response_model=list[FundingOut])
def list_funding(_: Principal = Depends(require("catalog.view")), session: Session = Depends(get_session)):
    return [FundingOut.model_validate(f, from_attributes=True)
            for f in session.scalars(select(FundingSource).order_by(FundingSource.code))]


@router.post("/funding-sources", response_model=FundingOut, status_code=201)
def create_funding(body: FundingIn, p: Principal = Depends(require("catalog.manage")),
                   meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    f = service.create_funding_source(session, body.code, body.name)
    session.commit()
    return FundingOut.model_validate(f, from_attributes=True)


# --- الموردون ----------------------------------------------------------------
@router.get("/suppliers", response_model=Page[SupplierOut])
def list_suppliers(q: str | None = Query(default=None, max_length=100), params: PageParams = Depends(),
                   _: Principal = Depends(require("catalog.view")), session: Session = Depends(get_session)):
    stmt = select(Supplier).order_by(Supplier.name)
    if q:
        norm = normalize_party_name(q)
        stmt = stmt.where(or_(Supplier.name_normalized.contains(norm), Supplier.tax_no == q))
    return paginate(session, stmt, params, lambda r: SupplierOut.model_validate(r[0], from_attributes=True))


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(body: SupplierIn, p: Principal = Depends(require("suppliers.manage")),
                    meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    s = service.create_supplier(session, **body.model_dump())
    session.commit()
    return SupplierOut.model_validate(s, from_attributes=True)
