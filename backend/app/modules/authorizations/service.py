"""التفويضات وتوزيعها على البنود (FR-AU)."""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.authorizations.models import Authorization, AuthorizationAllocation
from app.modules.catalog.models import Entity, FundingSource
from app.modules.documents.common import ensure_editable, require_line_scope, year_for_new_document
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest
from app.modules.users.models import RolePermission, UserRole

SOURCE_TYPE = "authorization"
ZERO = Decimal("0")


def get(session: Session, auth_id: uuid.UUID) -> Authorization:
    a = session.get(Authorization, auth_id)
    if a is None:
        raise NotFound("التفويض غير موجود.")
    return a


def allocations_of(session: Session, auth_id: uuid.UUID) -> list[AuthorizationAllocation]:
    return list(session.scalars(select(AuthorizationAllocation)
                                .where(AuthorizationAllocation.authorization_id == auth_id)))


def allocated_total(session: Session, auth: Authorization) -> Decimal:
    return sum((a.amount for a in allocations_of(session, auth.id)), ZERO)


def _validate_header(session: Session, entity_id: uuid.UUID, funding_source_id: uuid.UUID | None,
                     period_from: date | None, period_to: date | None) -> None:
    e = session.get(Entity, entity_id)
    if e is None or not e.is_active:
        raise ValidationFailed("الجهة غير موجودة أو غير نشطة.", code="UNKNOWN_ENTITY")
    if funding_source_id and session.get(FundingSource, funding_source_id) is None:
        raise ValidationFailed("مصدر التمويل غير موجود.", code="UNKNOWN_FUNDING_SOURCE")
    if period_from and period_to and period_to < period_from:
        raise ValidationFailed("نهاية فترة التفويض قبل بدايتها.", code="INVALID_RANGE")


def _set_allocations(session: Session, principal: Principal, auth: Authorization, allocations: list[dict]) -> None:
    session.execute(delete(AuthorizationAllocation).where(AuthorizationAllocation.authorization_id == auth.id))
    seen = set()
    for a in allocations:
        bl = engine.get_or_create_line(session, auth.fiscal_year_id, auth.entity_id, a["item_id"])
        require_line_scope(session, principal, bl)
        if bl.id in seen:
            raise ValidationFailed("البند مكرر في التوزيع.", code="DUPLICATE_LINE")
        seen.add(bl.id)
        session.add(AuthorizationAllocation(authorization_id=auth.id, budget_line_id=bl.id, amount=a["amount"]))
    session.flush()


def _check_over_allocation_reason(session: Session, auth: Authorization) -> None:
    if allocated_total(session, auth) > auth.amount and not (auth.over_allocation_reason or "").strip():
        raise ValidationFailed("مجموع التوزيعات يتجاوز قيمة التفويض؛ أدخل سبب التجاوز ليعرض على المعتمد.",
                               code="OVER_ALLOCATION_REASON_REQUIRED")


def create_draft(session: Session, principal: Principal, *, fiscal_year_id: uuid.UUID, auth_no: str, auth_type: str,
                 auth_date: date, entity_id: uuid.UUID, period_from: date | None, period_to: date | None,
                 amount: Decimal, purpose: str, funding_source_id: uuid.UUID | None,
                 over_allocation_reason: str | None, allocations: list[dict]) -> Authorization:
    fy = year_for_new_document(session, fiscal_year_id, auth_date, principal)
    principal.require_scope("ENTITY", entity_id)
    _validate_header(session, entity_id, funding_source_id, period_from, period_to)
    auth_no = auth_no.strip()
    if session.scalar(select(exists().where(Authorization.fiscal_year_id == fy.id, Authorization.auth_type == auth_type,
                                            Authorization.auth_no == auth_no,
                                            Authorization.status.not_in(("CANCELLED", "REJECTED"))))):
        raise Conflict(f"التفويض رقم {auth_no} مسجل مسبقًا لهذه السنة.", code="DUPLICATE_AUTHORIZATION")
    auth = Authorization(id=uuid.uuid4(), fiscal_year_id=fy.id, auth_no=auth_no, auth_type=auth_type,
                         auth_date=auth_date, entity_id=entity_id, period_from=period_from, period_to=period_to,
                         amount=amount, purpose=purpose.strip(), funding_source_id=funding_source_id,
                         over_allocation_reason=over_allocation_reason, created_by=principal.user_id)
    session.add(auth)
    session.flush()
    _set_allocations(session, principal, auth, allocations)
    _check_over_allocation_reason(session, auth)
    return auth


def update_draft(session: Session, principal: Principal, auth: Authorization, changes: dict,
                 expected_version: int | None) -> Authorization:
    ensure_editable(auth, expected_version)
    if auth.created_by != principal.user_id:
        raise Conflict("يعدّل المسودة منشئها فقط.", code="NOT_OWNER", status_code=403)
    if "auth_date" in changes:
        year_for_new_document(session, auth.fiscal_year_id, changes["auth_date"], principal)
    for k in ("auth_date", "period_from", "period_to", "amount", "purpose", "funding_source_id",
              "over_allocation_reason"):
        if k in changes:
            setattr(auth, k, changes[k])
    _validate_header(session, auth.entity_id, auth.funding_source_id, auth.period_from, auth.period_to)
    if "allocations" in changes:
        _set_allocations(session, principal, auth, changes["allocations"])
    _check_over_allocation_reason(session, auth)
    auth.row_version += 1
    session.flush()
    return auth


def build_posting(session: Session, auth: Authorization, posted_by: uuid.UUID,
                  approved_by: uuid.UUID | None) -> PostingRequest:
    allocs = allocations_of(session, auth.id)
    if not allocs:
        raise ValidationFailed("لا يمكن ترحيل تفويض بدون توزيع على البنود.", code="NO_ALLOCATIONS")
    specs = [EntrySpec(budget_line_id=a.budget_line_id, txn_type="BUDGET_ALLOCATION", component="ALLOCATION",
                       direction=1, amount=a.amount, source_line_id=a.id,
                       description=f"تفويض {auth.auth_no}: {auth.purpose}") for a in allocs]
    return PostingRequest(fiscal_year_id=auth.fiscal_year_id, entry_date=auth.auth_date, source_type=SOURCE_TYPE,
                          source_id=auth.id, posted_by=posted_by, approved_by=approved_by, entries=specs,
                          document_no=auth.auth_no)


def _has_perm(session: Session, user_id: uuid.UUID, perm: str) -> bool:
    return bool(session.scalar(select(exists().where(UserRole.user_id == user_id,
                                                     RolePermission.role_id == UserRole.role_id,
                                                     RolePermission.permission_code == perm))))


def post(session: Session, auth: Authorization, actor: uuid.UUID, override_grant_id: uuid.UUID | None = None,
         *, historical_exception: bool = False, date_is_estimated: bool = False) -> engine.PostingResult:
    if auth.status in ("POSTED", "REVERSED", "CANCELLED", "REJECTED"):
        raise Conflict("التفويض مرحّل أو في حالة نهائية.", code="INVALID_STATE")
    if allocated_total(session, auth) > auth.amount:
        if not historical_exception and not _has_perm(session, actor, "authorizations.over_allocate"):
            raise Conflict("مجموع التوزيعات يتجاوز قيمة التفويض ويتطلب صلاحية خاصة.", code="OVER_ALLOCATION")
        auth.over_allocation_approved_by = actor
    req = build_posting(session, auth, actor, actor)
    req.override_grant_id = override_grant_id
    req.historical_exception = historical_exception
    req.date_is_estimated = date_is_estimated
    result = engine.post(session, req)
    auth.status, auth.posted_by, auth.posted_at = "POSTED", actor, datetime.now(UTC)
    auth.row_version += 1
    session.flush()
    return result
