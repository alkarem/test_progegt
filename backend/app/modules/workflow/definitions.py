"""مسارات الموافقة الافتراضية (05-workflow §3). تُزامَن مع قاعدة البيانات دون المساس بتعديلات المسؤول."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.workflow.models import WorkflowDefinition, WorkflowStep

REVIEW = ("FINANCIAL_REVIEW", "المراجعة المالية", "review", False, False)
CONTROL = ("BUDGET_CONTROL", "رقابة الميزانية", "control", True, False)
SUPERVISE = ("SUPERVISOR_APPROVAL", "اعتماد المشرف", "supervise", False, False)
FINAL = ("FINAL_APPROVAL", "الاعتماد النهائي والترحيل", "approve", True, True)
SUPERVISE_POST = ("SUPERVISOR_APPROVAL", "اعتماد المشرف والترحيل", "supervise", True, True)

# رمز المسار ← (نوع المصدر، الاسم، المراحل، حد أدنى اختياري لمرحلة المشرف)
DEFAULTS: dict[str, tuple[str, str, list[tuple], Decimal | None]] = {
    "budget_document": ("budget_document", "الاعتماد الأصلي والتعزيز والتخفيض", [REVIEW, CONTROL, SUPERVISE, FINAL], None),
    "authorization": ("authorization", "التفويضات", [REVIEW, CONTROL, FINAL], None),
    "transfer": ("transfer", "المناقلات", [REVIEW, CONTROL, SUPERVISE, FINAL], None),
    "purchase_request": ("commitment", "طلبات الشراء (حجز مبدئي)", [CONTROL, SUPERVISE_POST], None),
    "commitment": ("commitment", "الارتباطات", [REVIEW, CONTROL, SUPERVISE, FINAL], None),
    "expenditure": ("expenditure", "المصروفات", [REVIEW, CONTROL, SUPERVISE, FINAL], Decimal("5000")),
    "adjustment": ("adjustment", "التسويات والقيود العكسية", [REVIEW, CONTROL, SUPERVISE, FINAL], None),
}


def sync_workflow_definitions(session: Session) -> None:
    for code, (source_type, name, steps, supervise_min) in DEFAULTS.items():
        if session.scalar(select(WorkflowDefinition.id).where(WorkflowDefinition.code == code)):
            continue
        d = WorkflowDefinition(code=code, source_type=source_type, name=name)
        session.add(d)
        session.flush()
        for seq, (scode, sname, action, check, posts) in enumerate(steps, start=1):
            session.add(WorkflowStep(definition_id=d.id, seq=seq, code=scode, name=sname, action=action,
                                     runs_budget_check=check, posts=posts,
                                     min_amount=supervise_min if action == "supervise" and not posts else None))
    session.flush()
