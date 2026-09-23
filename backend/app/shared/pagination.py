from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        self.page = page
        self.page_size = page_size


def paginate(session: Session, stmt: Select, params: PageParams, mapper) -> dict:
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = session.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))
    return {"items": [mapper(r) for r in rows], "total": total, "page": params.page, "page_size": params.page_size}
