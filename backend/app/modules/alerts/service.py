"""محرك التنبيهات (FR-AL) ومركز الإشعارات.

قواعد حتمية كلها (لا ذكاء اصطناعي هنا، 12-ai §3). تُقيَّم:
- فورًا بعد كل ترحيل أو تقديم (أسطر الميزانية والمستند المعني).
- دوريًا (run_periodic): المهل، والارتباطات القديمة، والتفويضات غير الموزعة، ومطابقة الأرصدة.
التنبيه المفتوح لا يتكرر (dedup_key)، وتنبيهات الرصيد تُغلق تلقائيًا إذا زال سببها.
"""
import statistics
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.modules.alerts.models import Alert, AlertRule, Notification
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalYear
from app.modules.ledger import engine
from app.modules.ledger.models import BudgetLine, LedgerEntry
from app.modules.users.models import RolePermission, User, UserRole
from app.shared.money import fmt

AUTO_RESOLVE = {"BUDGET_EXCEEDED", "LOW_BALANCE", "BALANCE_MISMATCH"}


def _now() -> datetime:
    return datetime.now(UTC)


def _rule(session: Session, code: str) -> AlertRule | None:
    r = session.get(AlertRule, code)
    return r if r and r.is_active else None


def users_with_permission(session: Session, perm: str) -> list[uuid.UUID]:
    return list(session.scalars(select(User.id).join(UserRole, UserRole.user_id == User.id).join(
        RolePermission, RolePermission.role_id == UserRole.role_id).where(
        RolePermission.permission_code == perm, User.is_active.is_(True), User.deleted_at.is_(None)).distinct()))


def notify(session: Session, user_ids, title: str, body: str | None = None, link: str | None = None,
           alert_id: uuid.UUID | None = None) -> None:
    for uid in set(user_ids):
        session.add(Notification(user_id=uid, title=title[:200], body=body, link=link, alert_id=alert_id))
    session.flush()


def raise_alert(session: Session, code: str, message: str, *, dedup: str, severity: str | None = None,
                fiscal_year_id=None, budget_line_id=None, source_type=None, source_id=None,
                details: dict | None = None) -> Alert | None:
    rule = _rule(session, code)
    if rule is None:
        return None
    key = f"{code}:{dedup}"
    existing = session.scalar(select(Alert).where(Alert.dedup_key == key, Alert.status.in_(("OPEN", "ACKNOWLEDGED"))))
    if existing:
        if severity and severity != existing.severity:
            existing.severity, existing.message, existing.details = severity, message, details
        return existing
    a = Alert(rule_code=code, severity=severity or rule.severity, fiscal_year_id=fiscal_year_id,
              budget_line_id=budget_line_id, source_type=source_type, source_id=source_id, message=message,
              details=details, dedup_key=key)
    session.add(a)
    session.flush()
    if a.severity in ("HIGH", "CRITICAL"):
        notify(session, users_with_permission(session, "alerts.manage"), f"⚠ {message}", link=_link(a), alert_id=a.id)
    return a


def _link(a: Alert) -> str | None:
    if a.budget_line_id:
        return f"/budget/lines/{a.budget_line_id}"
    if a.source_type and a.source_id:
        return f"/documents/{a.source_type}/{a.source_id}"
    return None


def resolve_auto(session: Session, code: str, dedup: str) -> None:
    for a in session.scalars(select(Alert).where(Alert.dedup_key == f"{code}:{dedup}",
                                                 Alert.status.in_(("OPEN", "ACKNOWLEDGED")))):
        a.status, a.resolved_at, a.resolution = "RESOLVED", _now(), "زال السبب تلقائيًا"


# ---------------------------------------------------------------------------
# قواعد الرصيد
# ---------------------------------------------------------------------------
def evaluate_line(session: Session, line_id: uuid.UUID) -> None:
    line = session.get(BudgetLine, line_id)
    pos = engine.current_position(session, line)
    item = session.get(BudgetItem, line.item_id)
    label = f"بند {item.code} {item.name}"
    key = str(line_id)
    if pos.available < 0:
        raise_alert(session, "BUDGET_EXCEEDED", f"{label}: تجاوز الاعتماد بقيمة {fmt(-pos.available)}",
                    dedup=key, fiscal_year_id=line.fiscal_year_id, budget_line_id=line.id,
                    details={"available": str(pos.available)})
    else:
        resolve_auto(session, "BUDGET_EXCEEDED", key)
    rule = _rule(session, "LOW_BALANCE")
    rate = pos.utilization_rate
    if rule and rate is not None and pos.available >= 0:
        warn, high = Decimal(str(rule.params.get("warning", 80))), Decimal(str(rule.params.get("high", 90)))
        if rate >= warn:
            raise_alert(session, "LOW_BALANCE", f"{label}: وصل الاستخدام إلى {rate}%", dedup=key,
                        severity="HIGH" if rate >= high else "WARNING", fiscal_year_id=line.fiscal_year_id,
                        budget_line_id=line.id, details={"utilization_rate": str(rate),
                                                         "available": str(pos.available)})
        else:
            resolve_auto(session, "LOW_BALANCE", key)


def evaluate_unusual(session: Session, source_type: str, source_id: uuid.UUID) -> None:
    """مبلغ غير معتاد: Robust z-score (الوسيط/MAD) على مصروفات البند نفسه (12-ai §3)."""
    rule = _rule(session, "UNUSUAL_TRANSACTION")
    if rule is None:
        return
    for e in session.scalars(select(LedgerEntry).where(LedgerEntry.source_type == source_type,
                                                       LedgerEntry.source_id == source_id,
                                                       LedgerEntry.txn_type == "ACTUAL_EXPENDITURE")):
        line = session.get(BudgetLine, e.budget_line_id)
        history = [float(x) for x in session.scalars(select(LedgerEntry.amount).join(
            BudgetLine, BudgetLine.id == LedgerEntry.budget_line_id).where(
            BudgetLine.item_id == line.item_id, LedgerEntry.txn_type == "ACTUAL_EXPENDITURE",
            LedgerEntry.id != e.id))]
        if len(history) < int(rule.params.get("min_history", 5)):
            continue
        med = statistics.median(history)
        mad = statistics.median([abs(x - med) for x in history]) or 1e-9
        z = 0.6745 * (float(e.amount) - med) / mad
        if z >= float(rule.params.get("z", 3.5)):
            raise_alert(session, "UNUSUAL_TRANSACTION",
                        f"مبلغ غير معتاد ({fmt(e.amount)}) مقارنة بمصروفات البند (الوسيط {med:,.3f})",
                        dedup=str(e.id), fiscal_year_id=e.fiscal_year_id, budget_line_id=e.budget_line_id,
                        source_type=source_type, source_id=source_id,
                        details={"amount": str(e.amount), "median": f"{med:.3f}", "robust_z": round(z, 2)})


def after_posting(session: Session, source_type: str, doc, line_ids: list[uuid.UUID]) -> None:
    for lid in set(line_ids):
        evaluate_line(session, lid)
    if source_type == "expenditure":
        evaluate_unusual(session, source_type, doc.id)
        evaluate_patterns(session, doc)
        from app.modules.expenditures.service import similar
        dups = similar(session, doc)
        if dups:
            raise_alert(session, "DUPLICATE_DOCUMENT",
                        f"مصروف {doc.document_no} مشابه لمستندات سابقة: {', '.join(d.document_no for d in dups)}",
                        dedup=str(doc.id), fiscal_year_id=doc.fiscal_year_id, budget_line_id=doc.budget_line_id,
                        source_type=source_type, source_id=doc.id)


# ---------------------------------------------------------------------------
# أنماط غير معتادة إضافية (12-ai §3) — حتمية بالكامل
# ---------------------------------------------------------------------------
def _posted_expenditures(session: Session):
    from app.modules.expenditures.models import Expenditure
    return select(Expenditure).where(Expenditure.status == "POSTED")


def evaluate_patterns(session: Session, e) -> None:
    """تجزئة المشتريات، الأرقام المستديرة المتكررة، والصرف بلا ارتباط لبنود يُتوقع فيها ارتباط."""
    from app.modules.expenditures.models import Expenditure
    item = session.scalar(select(BudgetItem).join(BudgetLine, BudgetLine.item_id == BudgetItem.id)
                          .where(BudgetLine.id == e.budget_line_id))

    rule = _rule(session, "SPLIT_PURCHASE")
    if rule and e.supplier_id:
        limit = Decimal(str(rule.params.get("threshold", "5000")))
        days = int(rule.params.get("days", 7))
        group = list(session.scalars(_posted_expenditures(session).where(
            Expenditure.supplier_id == e.supplier_id, Expenditure.budget_line_id == e.budget_line_id,
            Expenditure.expenditure_date.between(e.expenditure_date - timedelta(days=days),
                                                 e.expenditure_date + timedelta(days=days)),
            Expenditure.amount < limit)))
        total = sum((x.amount for x in group), Decimal("0"))
        if len(group) >= 2 and total > limit and e.amount < limit:
            raise_alert(session, "SPLIT_PURCHASE",
                        f"تجزئة محتملة: {len(group)} مصروفات لنفس المستفيد على البند {item.code} خلال {days} أيام،"
                        f" كل منها دون {fmt(limit)} ومجموعها {fmt(total)}",
                        dedup=f"{e.supplier_id}:{e.budget_line_id}:{min(x.expenditure_date for x in group)}",
                        fiscal_year_id=e.fiscal_year_id, budget_line_id=e.budget_line_id,
                        source_type="expenditure", source_id=e.id,
                        details={"documents": sorted(x.document_no for x in group), "total": str(total),
                                 "threshold": str(limit)})

    rule = _rule(session, "ROUND_AMOUNTS")
    if rule and e.supplier_id:
        unit = Decimal(str(rule.params.get("multiple", "1000")))
        min_amount = Decimal(str(rule.params.get("min_amount", "5000")))
        min_count = int(rule.params.get("min_count", 3))
        rounds = [x for x in session.scalars(_posted_expenditures(session).where(
            Expenditure.supplier_id == e.supplier_id, Expenditure.fiscal_year_id == e.fiscal_year_id,
            Expenditure.amount >= min_amount)) if x.amount % unit == 0]
        if len(rounds) >= min_count and e.amount % unit == 0 and e.amount >= min_amount:
            raise_alert(session, "ROUND_AMOUNTS",
                        f"{len(rounds)} مبالغ مستديرة (مضاعفات {fmt(unit)}) لنفس المستفيد في السنة",
                        dedup=f"{e.supplier_id}:{e.fiscal_year_id}", fiscal_year_id=e.fiscal_year_id,
                        source_type="expenditure", source_id=e.id,
                        details={"documents": sorted(x.document_no for x in rounds)})

    rule = _rule(session, "EXPENSE_WITHOUT_COMMITMENT")
    if rule and e.commitment_id is None and item.code in rule.params.get("item_codes", []):
        limit = Decimal(str(rule.params.get("threshold", "10000")))
        if e.amount >= limit:
            raise_alert(session, "EXPENSE_WITHOUT_COMMITMENT",
                        f"مصروف {e.document_no} بمبلغ {fmt(e.amount)} على البند {item.code} دون ارتباط مسبق",
                        dedup=str(e.id), fiscal_year_id=e.fiscal_year_id, budget_line_id=e.budget_line_id,
                        source_type="expenditure", source_id=e.id)


def evaluate_year_end(session: Session, fy: FiscalYear, today) -> bool:
    """ارتفاع الصرف في آخر N يومًا عن متوسط السنة بأكثر من الضعف (يُقيَّم فقط بعد بدء تلك الفترة)."""
    rule = _rule(session, "YEAR_END_SPIKE")
    if rule is None:
        return False
    window = int(rule.params.get("days", 15))
    factor = Decimal(str(rule.params.get("factor", "2")))
    start_tail = fy.end_date - timedelta(days=window - 1)
    if today < start_tail:
        return False
    rows = session.execute(select(LedgerEntry.entry_date, LedgerEntry.amount, LedgerEntry.direction).where(
        LedgerEntry.fiscal_year_id == fy.id, LedgerEntry.component == "ACTUAL")).all()
    tail = sum((a * d for dt, a, d in rows if dt >= start_tail), Decimal("0"))
    body = sum((a * d for dt, a, d in rows if dt < start_tail), Decimal("0"))
    body_days = max((start_tail - fy.start_date).days, 1)
    tail_days = (min(today, fy.end_date) - start_tail).days + 1
    if body <= 0 or tail <= 0:
        return False
    tail_avg, body_avg = tail / tail_days, body / body_days
    if tail_avg > factor * body_avg:
        raise_alert(session, "YEAR_END_SPIKE",
                    f"متوسط الصرف اليومي في آخر {window} يومًا من {fy.year} ({fmt(tail_avg)}) يتجاوز"
                    f" {factor} ضعف متوسط السنة ({fmt(body_avg)})",
                    dedup=str(fy.id), fiscal_year_id=fy.id,
                    details={"tail_total": str(tail), "body_total": str(body)})
        return True
    return False


def after_submit(session: Session, source_type: str, doc, doc_no: str) -> None:
    from app.modules.attachments.service import count_for_source
    if count_for_source(session, source_type, doc.id) == 0:
        raise_alert(session, "MISSING_ATTACHMENT", f"المستند {doc_no} قُدّم بلا مرفقات مؤيدة", dedup=str(doc.id),
                    fiscal_year_id=doc.fiscal_year_id, source_type=source_type, source_id=doc.id)


def resolve_for_source(session: Session, code: str, source_id: uuid.UUID) -> None:
    resolve_auto(session, code, str(source_id))


# ---------------------------------------------------------------------------
# التقييم الدوري
# ---------------------------------------------------------------------------
def run_periodic(session: Session) -> dict:
    counts: dict[str, int] = {}

    def bump(code):
        counts[code] = counts.get(code, 0) + 1

    now = _now()
    open_years = [fy.id for fy in session.scalars(select(FiscalYear).where(FiscalYear.status.in_(("OPEN", "CLOSING"))))]
    for lid in session.scalars(select(BudgetLine.id).where(BudgetLine.fiscal_year_id.in_(open_years))):
        evaluate_line(session, lid)

    from app.modules.workflow.models import WorkflowInstance, WorkflowStep
    for inst, step in session.execute(select(WorkflowInstance, WorkflowStep).join(
            WorkflowStep, WorkflowStep.id == WorkflowInstance.current_step_id).where(WorkflowInstance.state == "ACTIVE")):
        if now - inst.step_entered_at >= timedelta(days=step.sla_days):
            raise_alert(session, "APPROVAL_OVERDUE", f"مستند ينتظر «{step.name}» منذ أكثر من {step.sla_days} أيام",
                        dedup=f"{inst.id}:{step.id}:{inst.round}", fiscal_year_id=inst.fiscal_year_id,
                        source_type=inst.source_type, source_id=inst.source_id)
            bump("APPROVAL_OVERDUE")
        if inst.source_type == "transfer":
            rule = _rule(session, "PENDING_TRANSFER")
            if rule and now - inst.started_at >= timedelta(days=int(rule.params.get("days", 7))):
                raise_alert(session, "PENDING_TRANSFER", "توجد مناقلة لم تكتمل موافقتها", dedup=str(inst.source_id),
                            fiscal_year_id=inst.fiscal_year_id, source_type="transfer", source_id=inst.source_id)
                bump("PENDING_TRANSFER")

    from app.modules.commitments import service as cm
    from app.modules.commitments.models import Commitment
    rule = _rule(session, "STALE_COMMITMENT")
    if rule:
        cutoff = (now - timedelta(days=int(rule.params.get("days", 90)))).date()
        for c in session.scalars(select(Commitment).where(
                Commitment.status == "POSTED", Commitment.commitment_status.in_(("APPROVED", "PARTIALLY_PAID")),
                Commitment.commitment_date <= cutoff)):
            raise_alert(session, "STALE_COMMITMENT",
                        f"الارتباط {c.commitment_no} لم تتم تسويته (القائم {fmt(cm.outstanding(session, c))})",
                        dedup=str(c.id), fiscal_year_id=c.fiscal_year_id, budget_line_id=c.budget_line_id,
                        source_type="commitment", source_id=c.id)
            bump("STALE_COMMITMENT")

    from app.modules.authorizations import service as au
    from app.modules.authorizations.models import Authorization
    rule = _rule(session, "UNALLOCATED_AUTHORIZATION")
    if rule:
        cutoff = (now - timedelta(days=int(rule.params.get("days", 15)))).date()
        for a in session.scalars(select(Authorization).where(Authorization.status == "POSTED",
                                                             Authorization.auth_date <= cutoff)):
            gap = a.amount - au.allocated_total(session, a)
            if gap > 0:
                raise_alert(session, "UNALLOCATED_AUTHORIZATION",
                            f"التفويض {a.auth_no} غير موزع بالكامل (غير الموزع {fmt(gap)})", dedup=str(a.id),
                            fiscal_year_id=a.fiscal_year_id, source_type="authorization", source_id=a.id)
                bump("UNALLOCATED_AUTHORIZATION")

    for fy in session.scalars(select(FiscalYear).where(FiscalYear.id.in_(open_years))):
        if evaluate_year_end(session, fy, now.date()):
            bump("YEAR_END_SPIKE")

    mismatched = engine.reconcile(session)
    if mismatched:
        raise_alert(session, "BALANCE_MISMATCH", f"عدم تطابق الأرصدة مع القيود في {len(mismatched)} سطر",
                    dedup="global", details={"lines": [str(x) for x in mismatched]})
        bump("BALANCE_MISMATCH")
    else:
        resolve_auto(session, "BALANCE_MISMATCH", "global")
    session.flush()
    return counts


def has_open(session: Session, code: str, **filters) -> bool:
    stmt = select(exists().where(Alert.rule_code == code, Alert.status.in_(("OPEN", "ACKNOWLEDGED")),
                                 *[getattr(Alert, k) == v for k, v in filters.items()]))
    return bool(session.scalar(stmt))
