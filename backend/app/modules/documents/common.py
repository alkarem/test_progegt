"""أدوات مشتركة لكل المستندات المالية: التحقق من السنة والتاريخ والنطاق والقفل التفاؤلي."""
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, ValidationFailed
from app.modules.fiscal.models import FiscalYear
from app.modules.fiscal.service import get_year
from app.modules.ledger.models import BudgetLine

EDITABLE = ("DRAFT", "RETURNED")


def year_for_new_document(session: Session, fiscal_year_id: uuid.UUID, doc_date: date,
                          principal: Principal) -> FiscalYear:
    fy = get_year(session, fiscal_year_id)
    principal.require_scope("FISCAL_YEAR", fy.id)
    if fy.status in ("CLOSING", "CLOSED"):
        raise Conflict(f"السنة المالية {fy.year} مقفلة أو قيد الإقفال.", code="YEAR_CLOSED")
    if not (fy.start_date <= doc_date <= fy.end_date):
        raise ValidationFailed(f"تاريخ المستند خارج السنة المالية {fy.year}.", code="DATE_OUTSIDE_YEAR")
    return fy


def require_line_scope(session: Session, principal: Principal, line: BudgetLine) -> None:
    from app.modules.catalog.models import BudgetItem
    principal.require_scope("ENTITY", line.entity_id)
    principal.require_scope("ITEM", line.item_id)
    if "CHAPTER" in principal.scopes:
        principal.require_scope("CHAPTER", session.get(BudgetItem, line.item_id).chapter_id)


def ensure_editable(doc, expected_version: int | None) -> None:
    if doc.status not in EDITABLE:
        raise Conflict("المستند غير قابل للتعديل في حالته الحالية.", code="NOT_EDITABLE",
                       details={"status": doc.status})
    if expected_version is not None and expected_version != doc.row_version:
        raise Conflict("عدّل مستخدم آخر المستند؛ أعد تحميله.", code="VERSION_CONFLICT", status_code=412,
                       details={"current_version": doc.row_version})
