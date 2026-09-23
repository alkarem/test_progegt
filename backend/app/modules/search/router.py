import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, get_principal
from app.modules.search import service
from app.shared.money import Money, jsonable

router = APIRouter(tags=["البحث الشامل"])


@router.get("/search")
def search(q: str | None = Query(default=None, max_length=100), type: list[str] | None = Query(default=None),
           fiscal_year_id: uuid.UUID | None = None, amount_min: Money | None = None, amount_max: Money | None = None,
           date_from: date | None = None, date_to: date | None = None, user_id: uuid.UUID | None = None,
           limit: int = Query(default=50, ge=1, le=200), p: Principal = Depends(get_principal),
           session: Session = Depends(get_session)):
    return jsonable(service.search(session, p, q, types=type, fiscal_year_id=fiscal_year_id, amount_min=amount_min,
                                   amount_max=amount_max, date_from=date_from, date_to=date_to, user_id=user_id,
                                   limit=limit))
