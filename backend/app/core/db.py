"""جلسات قاعدة البيانات وضبط سياق التدقيق لكل معاملة."""
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None
_url_override: str | None = None


def configure_database(url: str | None) -> None:
    """تحديد رابط قاعدة بيانات مختلف (الاختبارات)."""
    global _url_override
    reset_engine()
    _url_override = url


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(_url_override or get_settings().database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def reset_engine():
    """يُستخدم في الاختبارات عند تغيير رابط قاعدة البيانات."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def new_session() -> Session:
    get_engine()
    return _SessionLocal()


@dataclass
class AuditContext:
    user_id: UUID
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    reason: str | None = None


def set_audit_context(session: Session, ctx: AuditContext) -> None:
    """يضبط متغيرات المعاملة التي تقرؤها triggers التدقيق.

    يجب استدعاؤها في بداية كل معاملة كتابة؛ الجداول المالية ترفض الكتابة بدونها.
    القيم محلية للمعاملة (is_local=true) فتختفي بعد COMMIT/ROLLBACK.

    تأخذ أيضًا قفل سلسلة التدقيق أولًا، قبل أي قفل على صفوف الأرصدة، حتى يكون ترتيب
    الأقفال ثابتًا في كل معاملات الكتابة فلا يحدث جمود (deadlock). النتيجة أن معاملات
    الكتابة تُسلسل؛ وهي قصيرة (ميلي ثوانٍ) وهذا مقبول لحجم الاستخدام المتوقع.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(724242)"))
    session.execute(
        text(
            "SELECT set_config('gbcfms.user_id', :u, true), set_config('gbcfms.ip', :ip, true),"
            " set_config('gbcfms.user_agent', :ua, true), set_config('gbcfms.request_id', :rid, true),"
            " set_config('gbcfms.reason', :reason, true)"
        ),
        {
            "u": str(ctx.user_id),
            "ip": ctx.ip or "",
            "ua": (ctx.user_agent or "")[:500],
            "rid": ctx.request_id or "",
            "reason": ctx.reason or "",
        },
    )


def get_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()
