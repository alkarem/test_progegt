"""سجل أنواع المستندات: كل وحدة مستندات تسجل معالجًا يعرف كيف يُفحص المستند ويُرحَّل.

محرك الموافقات عام ولا يعرف تفاصيل أي مستند؛ يتعامل معها عبر هذه الواجهة فقط.
"""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DocHandler:
    source_type: str                 # 'budget_document' (يُستخدم في المسار /documents/{source_type}/…)
    perm_prefix: str                 # 'budget_documents' → budget_documents.review …
    label: str                       # اسم عربي للنوع
    get: Callable[[Session, uuid.UUID], Any]
    amount: Callable[[Session, Any], Decimal]
    line_ids: Callable[[Session, Any], list[uuid.UUID]]
    simulate: Callable[[Session, Any], dict]           # فحص الرصيد بدون كتابة
    post: Callable[..., Any]                           # (session, doc, actor_id, override_grant_id) → PostingResult
    definition_code: Callable[[Any], str]
    doc_no: Callable[[Any], str]
    on_cancel: Callable[[Session, Any], None] | None = None


_HANDLERS: dict[str, DocHandler] = {}


def register(handler: DocHandler) -> None:
    _HANDLERS[handler.source_type] = handler


def get_handler(source_type: str) -> DocHandler:
    from app.core.errors import NotFound
    h = _HANDLERS.get(source_type)
    if h is None:
        raise NotFound("نوع المستند غير معروف.", code="UNKNOWN_DOCUMENT_TYPE")
    return h


def all_handlers() -> list[DocHandler]:
    return list(_HANDLERS.values())
