"""واجهة المساعد الذكي (12-ai). كل النقاط تتطلب ai.use، وتعيد 503 إذا كان المساعد معطلًا."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_request_meta, require
from app.modules.ai import service
from app.modules.ai.models import AIInteraction
from app.shared.money import jsonable

router = APIRouter(prefix="/ai", tags=["المساعد الذكي"])


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    fiscal_year_id: uuid.UUID


class SummaryIn(BaseModel):
    fiscal_year_id: uuid.UUID


class HistoryRow(BaseModel):
    id: uuid.UUID
    kind: str
    question: str
    answer: str | None
    status: str
    created_at: datetime


@router.get("/status")
def status(_: Principal = Depends(require("ai.use"))):
    s = get_settings()
    return {"enabled": s.ai_enabled, "model": s.ai_model if s.ai_enabled else None,
            "masking_personal_data": s.ai_mask_personal, "questions_per_hour": s.ai_questions_per_hour}


def _run(fn, session: Session, p: Principal, meta: RequestMeta, reason: str, *args):
    begin_write(session, p.user_id, meta, reason=reason)
    try:
        result = fn(session, p, *args)
    except service.AIUnavailable:
        session.commit()   # يُحفظ سجل المحاولة الفاشلة
        raise
    session.commit()
    return jsonable(result)


@router.post("/ask")
def ask(body: AskIn, p: Principal = Depends(require("ai.use")), meta: RequestMeta = Depends(get_request_meta),
        session: Session = Depends(get_session)):
    return _run(service.ask, session, p, meta, "سؤال للمساعد الذكي", body.question, body.fiscal_year_id)


@router.post("/summary")
def summary(body: SummaryIn, p: Principal = Depends(require("ai.use", "reports.view")),
            meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    return _run(service.summary, session, p, meta, "مسودة ملخص إداري", body.fiscal_year_id)


@router.get("/history", response_model=list[HistoryRow])
def history(limit: int = Query(default=20, ge=1, le=100), p: Principal = Depends(require("ai.use")),
            session: Session = Depends(get_session)):
    rows = session.scalars(select(AIInteraction).where(AIInteraction.user_id == p.user_id)
                           .order_by(AIInteraction.created_at.desc()).limit(limit))
    return [HistoryRow.model_validate(r, from_attributes=True) for r in rows]
