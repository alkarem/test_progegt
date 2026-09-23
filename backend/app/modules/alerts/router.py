import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_principal, get_request_meta, require
from app.core.errors import Conflict, NotFound
from app.modules.alerts import service
from app.modules.alerts.models import Alert, AlertRule, Notification
from app.modules.ledger.models import BudgetLine
from app.shared.pagination import Page, PageParams, paginate

router = APIRouter(tags=["التنبيهات والإشعارات"])


class AlertOut(BaseModel):
    id: uuid.UUID
    rule_code: str
    severity: str
    status: str
    fiscal_year_id: uuid.UUID | None
    budget_line_id: uuid.UUID | None
    source_type: str | None
    source_id: uuid.UUID | None
    message: str
    details: dict | None
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=3, max_length=1000)


class RuleOut(BaseModel):
    code: str
    name: str
    severity: str
    params: dict
    is_active: bool


class RuleUpdate(BaseModel):
    severity: str | None = Field(default=None, pattern="^(INFO|WARNING|HIGH|CRITICAL)$")
    params: dict | None = None
    is_active: bool | None = None


class NotificationOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str | None
    link: str | None
    alert_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


@router.get("/alerts", response_model=Page[AlertOut])
def list_alerts(status: str | None = "OPEN", severity: str | None = None, rule_code: str | None = None,
                fiscal_year_id: uuid.UUID | None = None, params: PageParams = Depends(),
                p: Principal = Depends(require("alerts.view")), session: Session = Depends(get_session)):
    stmt = select(Alert).outerjoin(BudgetLine, BudgetLine.id == Alert.budget_line_id).order_by(Alert.created_at.desc())
    for col, value in ((Alert.status, status), (Alert.severity, severity), (Alert.rule_code, rule_code),
                       (Alert.fiscal_year_id, fiscal_year_id)):
        if value is not None:
            stmt = stmt.where(col == value)
    for scope, col in (("ENTITY", BudgetLine.entity_id), ("ITEM", BudgetLine.item_id)):
        if scope in p.scopes:
            stmt = stmt.where((Alert.budget_line_id.is_(None)) | col.in_(p.scopes[scope]))
    if "FISCAL_YEAR" in p.scopes:
        stmt = stmt.where((Alert.fiscal_year_id.is_(None)) | Alert.fiscal_year_id.in_(p.scopes["FISCAL_YEAR"]))
    return paginate(session, stmt, params, lambda r: AlertOut.model_validate(r[0], from_attributes=True))


def _alert(session: Session, alert_id: uuid.UUID) -> Alert:
    a = session.get(Alert, alert_id)
    if a is None:
        raise NotFound("التنبيه غير موجود.")
    return a


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def ack(alert_id: uuid.UUID, p: Principal = Depends(require("alerts.view")),
        meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    a = _alert(session, alert_id)
    if a.status != "OPEN":
        raise Conflict("التنبيه ليس مفتوحًا.", code="INVALID_STATE")
    a.status, a.acknowledged_by, a.acknowledged_at = "ACKNOWLEDGED", p.user_id, datetime.now(UTC)
    session.commit()
    return AlertOut.model_validate(a, from_attributes=True)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: uuid.UUID, body: ResolveIn, p: Principal = Depends(require("alerts.manage")),
            meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason=body.resolution)
    a = _alert(session, alert_id)
    if a.status not in ("OPEN", "ACKNOWLEDGED"):
        raise Conflict("التنبيه مغلق مسبقًا.", code="INVALID_STATE")
    a.status, a.resolved_by, a.resolved_at, a.resolution = "RESOLVED", p.user_id, datetime.now(UTC), body.resolution
    session.commit()
    return AlertOut.model_validate(a, from_attributes=True)


@router.post("/alerts/evaluate")
def evaluate(p: Principal = Depends(require("alerts.manage")), meta: RequestMeta = Depends(get_request_meta),
             session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta, reason="تقييم دوري للتنبيهات")
    counts = service.run_periodic(session)
    session.commit()
    return {"raised": counts}


@router.get("/alert-rules", response_model=list[RuleOut])
def rules(_: Principal = Depends(require("alerts.view")), session: Session = Depends(get_session)):
    return [RuleOut.model_validate(r, from_attributes=True) for r in session.scalars(select(AlertRule).order_by(AlertRule.code))]


@router.patch("/alert-rules/{code}", response_model=RuleOut)
def update_rule(code: str, body: RuleUpdate, p: Principal = Depends(require("alerts.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    r = session.get(AlertRule, code)
    if r is None:
        raise NotFound("القاعدة غير موجودة.")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    session.commit()
    return RuleOut.model_validate(r, from_attributes=True)


@router.get("/notifications", response_model=Page[NotificationOut])
def notifications(unread_only: bool = False, params: PageParams = Depends(), p: Principal = Depends(get_principal),
                  session: Session = Depends(get_session)):
    stmt = select(Notification).where(Notification.user_id == p.user_id).order_by(Notification.created_at.desc())
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paginate(session, stmt, params, lambda r: NotificationOut.model_validate(r[0], from_attributes=True))


@router.get("/notifications/unread-count")
def unread_count(p: Principal = Depends(get_principal), session: Session = Depends(get_session)):
    return {"count": session.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == p.user_id, Notification.read_at.is_(None)))}


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None


@router.post("/notifications/read", status_code=204)
def mark_read(body: ReadIn, p: Principal = Depends(get_principal), session: Session = Depends(get_session)):
    stmt = update(Notification).where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
    if body.ids:
        stmt = stmt.where(Notification.id.in_(body.ids))
    session.execute(stmt.values(read_at=datetime.now(UTC)))
    session.commit()
