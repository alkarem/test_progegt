"""محرك الموافقات (05-workflow): الانتقالات، والصلاحيات، وفصل المهام، والفحص، والترحيل."""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, DomainError, Forbidden, ValidationFailed
from app.modules.ledger.engine import INSUFFICIENT_MESSAGE, InsufficientBudget
from app.modules.ledger.models import BudgetLine
from app.modules.users.models import RolePermission, User, UserRole
from app.modules.workflow.models import (
    Delegation,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStep,
)
from app.modules.workflow.registry import DocHandler, all_handlers

IN_PROGRESS = ("SUBMITTED", "IN_REVIEW")


def _now() -> datetime:
    return datetime.now(UTC)


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (Decimal, uuid.UUID)):
        return str(obj)
    return obj


def _definition(session: Session, code: str) -> WorkflowDefinition:
    d = session.scalar(select(WorkflowDefinition).where(WorkflowDefinition.code == code,
                                                        WorkflowDefinition.is_active.is_(True)))
    if d is None:
        raise Conflict("لا يوجد مسار موافقة نشط لهذا النوع من المستندات.", code="NO_WORKFLOW")
    return d


def applicable_steps(session: Session, definition_id: uuid.UUID, amount: Decimal) -> list[WorkflowStep]:
    """المراحل التي تنطبق على المبلغ (WF-08). مرحلة الترحيل تنطبق دائمًا."""
    steps = session.scalars(select(WorkflowStep).where(WorkflowStep.definition_id == definition_id)
                            .order_by(WorkflowStep.seq)).all()
    return [s for s in steps if s.posts or ((s.min_amount is None or amount >= s.min_amount)
                                            and (s.max_amount is None or amount <= s.max_amount))]


def get_instance(session: Session, source_type: str, source_id: uuid.UUID) -> WorkflowInstance | None:
    return session.scalar(select(WorkflowInstance).where(WorkflowInstance.source_type == source_type,
                                                         WorkflowInstance.source_id == source_id))


def _record(session: Session, inst: WorkflowInstance, action: str, actor: uuid.UUID, *, step_id=None,
            on_behalf_of=None, comment=None, budget_check=None, ip=None) -> None:
    session.add(WorkflowAction(instance_id=inst.id, round=inst.round, step_id=step_id, action=action, actor_id=actor,
                               on_behalf_of=on_behalf_of, comment=comment,
                               budget_check=_json_safe(budget_check) if budget_check is not None else None, ip=ip))


def _require_doc_scope(session: Session, principal: Principal, handler: DocHandler, doc) -> None:
    principal.require_scope("FISCAL_YEAR", doc.fiscal_year_id)
    for lid in handler.line_ids(session, doc):
        line = session.get(BudgetLine, lid)
        principal.require_scope("ENTITY", line.entity_id)
        principal.require_scope("ITEM", line.item_id)


def _user_has_perm(session: Session, user_id: uuid.UUID, perm: str) -> bool:
    return bool(session.scalar(select(exists().where(
        UserRole.user_id == user_id, RolePermission.role_id == UserRole.role_id,
        RolePermission.permission_code == perm))))


def _delegator_for(session: Session, principal: Principal, perm: str) -> uuid.UUID | None:
    """WF-09: إذا فوّض مستخدمٌ صلاحياته للمستخدم الحالي في مدة سارية."""
    now = _now()
    for d in session.scalars(select(Delegation).where(Delegation.delegate_id == principal.user_id,
                                                      Delegation.revoked_at.is_(None),
                                                      Delegation.valid_from <= now, Delegation.valid_to >= now)):
        delegator = session.get(User, d.delegator_id)
        if delegator and delegator.is_active and _user_has_perm(session, d.delegator_id, perm):
            return d.delegator_id
    return None


def _authorize_step(session: Session, principal: Principal, handler: DocHandler, doc, inst: WorkflowInstance,
                    step: WorkflowStep) -> uuid.UUID | None:
    perm = f"{handler.perm_prefix}.{step.action}"
    on_behalf_of = None
    if not principal.has(perm):
        on_behalf_of = _delegator_for(session, principal, perm)
        if on_behalf_of is None:
            raise Forbidden(f"هذه المرحلة ({step.name}) تتطلب صلاحية لا تملكها.", code="PERMISSION_DENIED",
                            details={"permission": perm, "step": step.code})
    if inst.created_by in (principal.user_id, on_behalf_of):
        raise Forbidden("لا يمكنك اعتماد مستند أنشأته (فصل المهام).", code="SEGREGATION_OF_DUTIES")
    definition = session.get(WorkflowDefinition, inst.definition_id)
    if definition.separate_approvers:
        already = session.scalar(select(exists().where(
            WorkflowAction.instance_id == inst.id, WorkflowAction.round == inst.round,
            WorkflowAction.action == "APPROVE",
            or_(WorkflowAction.actor_id == principal.user_id,
                and_(WorkflowAction.on_behalf_of.is_not(None), WorkflowAction.on_behalf_of == on_behalf_of)))))
        if already:
            raise Forbidden("اعتمدت مرحلة سابقة لهذا المستند؛ يجب أن يعتمد هذه المرحلة مستخدم آخر.",
                            code="SEGREGATION_OF_DUTIES")
    _require_doc_scope(session, principal, handler, doc)
    return on_behalf_of


def _safe_simulate(session: Session, handler: DocHandler, doc) -> dict:
    try:
        return handler.simulate(session, doc)
    except DomainError as e:
        return {"ok": False, "message": e.message, "code": e.code, "details": e.details}


# ---------------------------------------------------------------------------
# الإجراءات
# ---------------------------------------------------------------------------
def submit(session: Session, principal: Principal, handler: DocHandler, doc, ip: str | None = None) -> WorkflowInstance:
    principal.require(f"{handler.perm_prefix}.submit")
    if doc.status not in ("DRAFT", "RETURNED"):
        raise Conflict("يمكن تقديم المسودة أو المستند المُرجَع فقط.", code="INVALID_STATE", details={"status": doc.status})
    if doc.created_by != principal.user_id:
        raise Forbidden("يقدّم المستند منشئه فقط.", code="NOT_OWNER")
    _require_doc_scope(session, principal, handler, doc)
    amount = handler.amount(session, doc)
    if amount <= 0:
        raise ValidationFailed("قيمة المستند يجب أن تكون أكبر من صفر.", code="INVALID_AMOUNT")
    definition = _definition(session, handler.definition_code(doc))
    steps = applicable_steps(session, definition.id, amount)
    inst = get_instance(session, handler.source_type, doc.id)
    if inst is None:
        inst = WorkflowInstance(id=uuid.uuid4(), definition_id=definition.id, source_type=handler.source_type,
                                source_id=doc.id, fiscal_year_id=doc.fiscal_year_id, state="ACTIVE", round=1,
                                amount=amount, created_by=doc.created_by, current_step_id=steps[0].id,
                                step_entered_at=_now())
        session.add(inst)
        session.flush()
    else:
        if inst.state not in ("RETURNED",):
            raise Conflict("المستند في دورة موافقة قائمة.", code="INVALID_STATE")
        inst.round += 1
        inst.state, inst.amount, inst.definition_id = "ACTIVE", amount, definition.id
        inst.current_step_id, inst.step_entered_at, inst.finished_at = steps[0].id, _now(), None
    _record(session, inst, "SUBMIT", principal.user_id, budget_check=_safe_simulate(session, handler, doc), ip=ip)
    doc.status = "SUBMITTED"
    doc.submitted_at = _now()
    doc.row_version += 1
    session.flush()
    return inst


def _active(session: Session, handler: DocHandler, doc) -> tuple[WorkflowInstance, WorkflowStep]:
    inst = get_instance(session, handler.source_type, doc.id)
    if inst is None or inst.state != "ACTIVE" or doc.status not in IN_PROGRESS:
        raise Conflict("المستند ليس في دورة موافقة نشطة.", code="INVALID_STATE", details={"status": doc.status})
    return inst, session.get(WorkflowStep, inst.current_step_id)


def approve(session: Session, principal: Principal, handler: DocHandler, doc, *, comment: str | None = None,
            override_grant_id: uuid.UUID | None = None, acknowledge_shortfall: bool = False,
            expected_step: str | None = None, ip: str | None = None):
    inst, step = _active(session, handler, doc)
    if expected_step and expected_step != step.code:
        raise Conflict("تغيرت مرحلة المستند؛ أعد تحميله.", code="STEP_CHANGED", details={"current_step": step.code})
    on_behalf_of = _authorize_step(session, principal, handler, doc, inst, step)

    if step.posts:
        # الفحص النهائي والترحيل داخل المعاملة نفسها، بقفل على الأرصدة (WF-06، WF-07)
        result = handler.post(session, doc, principal.user_id, override_grant_id)
        from app.modules.attachments.service import lock_for_source
        lock_for_source(session, handler.source_type, doc.id)
        _record(session, inst, "POST", principal.user_id, step_id=step.id, on_behalf_of=on_behalf_of,
                comment=comment, ip=ip, budget_check={"checks": result.checks,
                                                      "override_used": result.override_used})
        inst.state, inst.current_step_id, inst.finished_at = "COMPLETED", None, _now()
        session.flush()
        return inst

    check = None
    if step.runs_budget_check:
        check = handler.simulate(session, doc)
        if not check["ok"]:
            if not acknowledge_shortfall:
                first = next((ln for ln in check["lines"] if not ln["ok"]), {})
                raise InsufficientBudget(INSUFFICIENT_MESSAGE, details={**first, "lines": check["lines"],
                                                                       "override_possible": True})
            if not comment or len(comment.strip()) < 5:
                raise ValidationFailed("تمرير مستند بعجز في الرصيد يتطلب تعليقًا يوضح السبب.",
                                       code="COMMENT_REQUIRED")
            check["acknowledged_shortfall"] = True
    _record(session, inst, "APPROVE", principal.user_id, step_id=step.id, on_behalf_of=on_behalf_of,
            comment=comment, budget_check=check, ip=ip)
    steps = applicable_steps(session, inst.definition_id, inst.amount)
    idx = next(i for i, s in enumerate(steps) if s.id == step.id)
    inst.current_step_id = steps[idx + 1].id
    inst.step_entered_at = _now()
    doc.status = "IN_REVIEW"
    doc.row_version += 1
    session.flush()
    return inst


def _require_comment(comment: str | None) -> str:
    if not comment or len(comment.strip()) < 3:
        raise ValidationFailed("التعليق إلزامي لهذا الإجراء.", code="COMMENT_REQUIRED")
    return comment.strip()


def reject(session: Session, principal: Principal, handler: DocHandler, doc, comment: str | None,
           ip: str | None = None) -> WorkflowInstance:
    comment = _require_comment(comment)
    inst, step = _active(session, handler, doc)
    on_behalf_of = _authorize_step(session, principal, handler, doc, inst, step)
    _record(session, inst, "REJECT", principal.user_id, step_id=step.id, on_behalf_of=on_behalf_of,
            comment=comment, ip=ip)
    inst.state, inst.current_step_id, inst.finished_at = "REJECTED", None, _now()
    doc.status = "REJECTED"
    doc.row_version += 1
    if handler.on_cancel:
        handler.on_cancel(session, doc)
    session.flush()
    return inst


def return_to_creator(session: Session, principal: Principal, handler: DocHandler, doc, comment: str | None,
                      ip: str | None = None) -> WorkflowInstance:
    comment = _require_comment(comment)
    inst, step = _active(session, handler, doc)
    on_behalf_of = _authorize_step(session, principal, handler, doc, inst, step)
    _record(session, inst, "RETURN", principal.user_id, step_id=step.id, on_behalf_of=on_behalf_of,
            comment=comment, ip=ip)
    inst.state, inst.current_step_id = "RETURNED", None
    doc.status = "RETURNED"
    doc.row_version += 1
    session.flush()
    return inst


def cancel(session: Session, principal: Principal, handler: DocHandler, doc, comment: str | None,
           ip: str | None = None) -> None:
    comment = _require_comment(comment)
    inst = get_instance(session, handler.source_type, doc.id)
    if doc.status in ("DRAFT", "RETURNED"):
        if doc.created_by != principal.user_id and not principal.has(f"{handler.perm_prefix}.cancel"):
            raise Forbidden("يلغي المسودة منشئها أو مراقب الميزانية.", code="NOT_OWNER")
    elif doc.status in IN_PROGRESS:
        principal.require(f"{handler.perm_prefix}.cancel")
    else:
        raise Conflict("لا يمكن إلغاء مستند مرحّل أو منتهٍ؛ استخدم القيد العكسي.", code="INVALID_STATE",
                       details={"status": doc.status})
    if inst is not None:
        _record(session, inst, "CANCEL", principal.user_id, step_id=inst.current_step_id, comment=comment, ip=ip)
        inst.state, inst.current_step_id, inst.finished_at = "CANCELLED", None, _now()
    doc.status = "CANCELLED"
    doc.row_version += 1
    if handler.on_cancel:
        handler.on_cancel(session, doc)
    session.flush()


# ---------------------------------------------------------------------------
# الاستعلامات
# ---------------------------------------------------------------------------
def history(session: Session, handler: DocHandler, doc) -> list[dict]:
    inst = get_instance(session, handler.source_type, doc.id)
    if inst is None:
        return []
    rows = session.execute(select(WorkflowAction, User.full_name, WorkflowStep.name).join(
        User, User.id == WorkflowAction.actor_id).outerjoin(WorkflowStep, WorkflowStep.id == WorkflowAction.step_id)
        .where(WorkflowAction.instance_id == inst.id).order_by(WorkflowAction.id))
    return [{"id": a.id, "round": a.round, "action": a.action, "step": step_name, "actor_id": a.actor_id,
             "actor_name": name, "on_behalf_of": a.on_behalf_of, "comment": a.comment,
             "budget_check": a.budget_check, "acted_at": a.acted_at} for a, name, step_name in rows]


def status_of(session: Session, handler: DocHandler, doc) -> dict:
    inst = get_instance(session, handler.source_type, doc.id)
    step = session.get(WorkflowStep, inst.current_step_id) if inst and inst.current_step_id else None
    return {"state": inst.state if inst else None, "round": inst.round if inst else 0,
            "current_step": step.code if step else None, "current_step_name": step.name if step else None,
            "step_entered_at": inst.step_entered_at if inst and step else None}


def inbox(session: Session, principal: Principal) -> list[dict]:
    """صندوق المهام (FR-WF-05): المستندات التي يستطيع المستخدم إجراء المرحلة الحالية عليها."""
    handlers = {h.source_type: h for h in all_handlers()}
    out = []
    rows = session.execute(select(WorkflowInstance, WorkflowStep).join(
        WorkflowStep, WorkflowStep.id == WorkflowInstance.current_step_id).where(
        WorkflowInstance.state == "ACTIVE", WorkflowInstance.created_by != principal.user_id)
        .order_by(WorkflowInstance.step_entered_at))
    for inst, step in rows:
        h = handlers.get(inst.source_type)
        if h is None:
            continue
        perm = f"{h.perm_prefix}.{step.action}"
        via = None
        if not principal.has(perm):
            via = _delegator_for(session, principal, perm)
            if via is None:
                continue
        doc = h.get(session, inst.source_id)
        try:
            _require_doc_scope(session, principal, h, doc)
        except Forbidden:
            continue
        overdue = (_now() - inst.step_entered_at).days >= step.sla_days
        out.append({"source_type": inst.source_type, "source_id": inst.source_id, "doc_no": h.doc_no(doc),
                    "type_label": h.label, "amount": inst.amount, "step": step.code, "step_name": step.name,
                    "waiting_since": inst.step_entered_at, "overdue": overdue, "on_behalf_of": via})
    return out
