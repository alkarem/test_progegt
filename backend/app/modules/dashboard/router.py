import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, require
from app.modules.dashboard import service
from app.shared.money import jsonable

router = APIRouter(prefix="/dashboard", tags=["لوحة القيادة"])


@router.get("/kpis")
def kpis(fiscal_year_id: uuid.UUID, entity_id: uuid.UUID | None = None,
         p: Principal = Depends(require("budget.view")), session: Session = Depends(get_session)):
    p.require_scope("FISCAL_YEAR", fiscal_year_id)
    return jsonable(service.kpis(session, p, fiscal_year_id, entity_id))


@router.get("/charts")
def chart_catalog(_: Principal = Depends(require("budget.view"))):
    return service.CHARTS


@router.get("/charts/{name}")
def chart(name: str, fiscal_year_id: uuid.UUID, entity_id: uuid.UUID | None = None,
          p: Principal = Depends(require("budget.view")), session: Session = Depends(get_session)):
    p.require_scope("FISCAL_YEAR", fiscal_year_id)
    return jsonable(service.chart(session, p, name, fiscal_year_id, entity_id))
