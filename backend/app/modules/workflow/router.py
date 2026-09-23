import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import Principal, RequestMeta, begin_write, get_principal, get_request_meta, require
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.modules.users.models import User
from app.modules.workflow import service
from app.modules.workflow.models import Delegation, WorkflowDefinition, WorkflowStep
from app.modules.workflow.registry import get_handler
from app.shared.money import Money

router = APIRouter(tags=["الموافقات"])


class ActionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ApproveIn(ActionIn):
    override_grant_id: uuid.UUID | None = None
    acknowledge_shortfall: bool = False
    expected_step: str | None = Field(default=None, max_length=40)


class WorkflowStatusOut(BaseModel):
    source_type: str
    source_id: uuid.UUID
    doc_no: str
    doc_status: str
    state: str | None
    round: int
    current_step: str | None
    current_step_name: str | None
    step_entered_at: datetime | None


class HistoryRow(BaseModel):
    id: int
    round: int
    action: str
    step: str | None
    actor_id: uuid.UUID
    actor_name: str
    on_behalf_of: uuid.UUID | None
    comment: str | None
    budget_check: dict | None
    acted_at: datetime


class InboxRow(BaseModel):
    source_type: str
    source_id: uuid.UUID
    doc_no: str
    type_label: str
    amount: Money
    step: str
    step_name: str
    waiting_since: datetime
    overdue: bool
    on_behalf_of: uuid.UUID | None


class DelegationIn(BaseModel):
    delegate_id: uuid.UUID
    valid_from: datetime
    valid_to: datetime
    reason: str = Field(min_length=5, max_length=1000)


class DelegationOut(BaseModel):
    id: uuid.UUID
    delegator_id: uuid.UUID
    delegate_id: uuid.UUID
    valid_from: datetime
    valid_to: datetime
    reason: str
    revoked_at: datetime | None


class StepOut(BaseModel):
    id: uuid.UUID
    seq: int
    code: str
    name: str
    action: str
    min_amount: Money | None
    max_amount: Money | None
    runs_budget_check: bool
    posts: bool
    sla_days: int


class DefinitionOut(BaseModel):
    id: uuid.UUID
    code: str
    source_type: str
    name: str
    separate_approvers: bool
    is_active: bool
    steps: list[StepOut]


class StepUpdate(BaseModel):
    min_amount: Money | None = None
    max_amount: Money | None = None
    sla_days: int | None = Field(default=None, ge=1, le=90)


def _load(session: Session, p: Principal, source_type: str, doc_id: uuid.UUID):
    h = get_handler(source_type)
    p.require(f"{h.perm_prefix}.view")
    doc = h.get(session, doc_id)
    p.require_scope("FISCAL_YEAR", doc.fiscal_year_id)
    return h, doc


def _status(session: Session, h, doc) -> WorkflowStatusOut:
    return WorkflowStatusOut(source_type=h.source_type, source_id=doc.id, doc_no=h.doc_no(doc), doc_status=doc.status,
                             **service.status_of(session, h, doc))


def _guard_password(p: Principal):
    if p.must_change_password:
        raise Forbidden("يجب تغيير كلمة المرور أولًا.", code="PASSWORD_CHANGE_REQUIRED")


@router.get("/documents/{source_type}/{doc_id}/workflow", response_model=WorkflowStatusOut)
def workflow_status(source_type: str, doc_id: uuid.UUID, p: Principal = Depends(get_principal),
                    session: Session = Depends(get_session)):
    h, doc = _load(session, p, source_type, doc_id)
    return _status(session, h, doc)


@router.get("/documents/{source_type}/{doc_id}/history", response_model=list[HistoryRow])
def workflow_history(source_type: str, doc_id: uuid.UUID, p: Principal = Depends(get_principal),
                     session: Session = Depends(get_session)):
    h, doc = _load(session, p, source_type, doc_id)
    return service.history(session, h, doc)


def _act(action: str):
    def endpoint(source_type: str, doc_id: uuid.UUID, body: ApproveIn, p: Principal = Depends(get_principal),
                 meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
        _guard_password(p)
        begin_write(session, p.user_id, meta, reason=body.comment)
        h, doc = _load(session, p, source_type, doc_id)
        session.refresh(doc, with_for_update=True)  # تسلسل الإجراءات على المستند نفسه
        if action == "submit":
            service.submit(session, p, h, doc, meta.ip)
        elif action == "approve":
            service.approve(session, p, h, doc, comment=body.comment, override_grant_id=body.override_grant_id,
                            acknowledge_shortfall=body.acknowledge_shortfall, expected_step=body.expected_step,
                            ip=meta.ip)
        elif action == "reject":
            service.reject(session, p, h, doc, body.comment, meta.ip)
        elif action == "return":
            service.return_to_creator(session, p, h, doc, body.comment, meta.ip)
        elif action == "cancel":
            service.cancel(session, p, h, doc, body.comment, meta.ip)
        session.commit()
        return _status(session, h, doc)
    return endpoint


for _action in ("submit", "approve", "reject", "return", "cancel"):
    router.add_api_route(f"/documents/{{source_type}}/{{doc_id}}/{_action}", _act(_action), methods=["POST"],
                         response_model=WorkflowStatusOut, name=f"workflow_{_action}")


@router.get("/inbox", response_model=list[InboxRow])
def inbox(p: Principal = Depends(require("workflow.inbox")), session: Session = Depends(get_session)):
    return service.inbox(session, p)


# --- التفويض المؤقت (WF-09) ---------------------------------------------------
@router.get("/delegations", response_model=list[DelegationOut])
def list_delegations(p: Principal = Depends(require("workflow.inbox")), session: Session = Depends(get_session)):
    stmt = select(Delegation).where((Delegation.delegator_id == p.user_id) | (Delegation.delegate_id == p.user_id))
    return [DelegationOut.model_validate(d, from_attributes=True)
            for d in session.scalars(stmt.order_by(Delegation.created_at.desc()))]


@router.post("/delegations", response_model=DelegationOut, status_code=201)
def create_delegation(body: DelegationIn, p: Principal = Depends(require("workflow.inbox")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    if body.delegate_id == p.user_id:
        raise ValidationFailed("لا يمكن التفويض للنفس.", code="SELF_DELEGATION")
    if body.valid_to <= body.valid_from:
        raise ValidationFailed("مدة التفويض غير صالحة.", code="INVALID_RANGE")
    if (body.valid_to - body.valid_from).days > 90:
        raise ValidationFailed("أقصى مدة للتفويض 90 يومًا.", code="INVALID_RANGE")
    delegate = session.get(User, body.delegate_id)
    if delegate is None or not delegate.is_active or delegate.is_system:
        raise NotFound("المستخدم غير موجود.")
    begin_write(session, p.user_id, meta, reason=body.reason)
    d = Delegation(delegator_id=p.user_id, **body.model_dump())
    session.add(d)
    session.commit()
    return DelegationOut.model_validate(d, from_attributes=True)


@router.post("/delegations/{delegation_id}/revoke", response_model=DelegationOut)
def revoke_delegation(delegation_id: uuid.UUID, p: Principal = Depends(require("workflow.inbox")),
                      meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    begin_write(session, p.user_id, meta)
    d = session.get(Delegation, delegation_id)
    if d is None or d.delegator_id != p.user_id:
        raise NotFound("التفويض غير موجود.")
    if d.revoked_at:
        raise Conflict("التفويض ملغى مسبقًا.", code="INVALID_STATE")
    d.revoked_at = datetime.now(UTC)
    session.commit()
    return DelegationOut.model_validate(d, from_attributes=True)


# --- إعداد المسارات -----------------------------------------------------------
def _definition_out(session: Session, d: WorkflowDefinition) -> DefinitionOut:
    steps = session.scalars(select(WorkflowStep).where(WorkflowStep.definition_id == d.id).order_by(WorkflowStep.seq))
    return DefinitionOut(id=d.id, code=d.code, source_type=d.source_type, name=d.name,
                         separate_approvers=d.separate_approvers, is_active=d.is_active,
                         steps=[StepOut.model_validate(s, from_attributes=True) for s in steps])


@router.get("/workflow-definitions", response_model=list[DefinitionOut])
def list_definitions(_: Principal = Depends(require("workflow.inbox")), session: Session = Depends(get_session)):
    return [_definition_out(session, d) for d in session.scalars(select(WorkflowDefinition).order_by(WorkflowDefinition.code))]


@router.patch("/workflow-steps/{step_id}", response_model=StepOut)
def update_step(step_id: uuid.UUID, body: StepUpdate, p: Principal = Depends(require("workflow.manage")),
                meta: RequestMeta = Depends(get_request_meta), session: Session = Depends(get_session)):
    """شرائح المبالغ ومهلة المرحلة (WF-08، WF-10). المراحل نفسها وترتيبها ثابتة لضمان الرقابة."""
    begin_write(session, p.user_id, meta)
    step = session.get(WorkflowStep, step_id)
    if step is None:
        raise NotFound("المرحلة غير موجودة.")
    changes = body.model_dump(exclude_unset=True)
    if step.posts and ({"min_amount", "max_amount"} & changes.keys()):
        raise Conflict("مرحلة الترحيل تنطبق على كل المبالغ ولا تقبل شرائح.", code="POSTING_STEP_FIXED")
    if step.action == "control" and ({"min_amount", "max_amount"} & changes.keys()):
        raise Conflict("مرحلة رقابة الميزانية إلزامية لكل المبالغ.", code="CONTROL_STEP_FIXED")
    for k, v in changes.items():
        setattr(step, k, v)
    if step.min_amount is not None and step.max_amount is not None and step.max_amount < step.min_amount:
        raise ValidationFailed("الحد الأعلى أقل من الأدنى.", code="INVALID_RANGE")
    session.commit()
    return StepOut.model_validate(step, from_attributes=True)

