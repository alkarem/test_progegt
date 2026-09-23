"""بنية الاختبار: PostgreSQL حقيقي. قاعدة قالب واحدة بعد الترحيلات، ونسخة جديدة منها لكل اختبار."""
import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from app.core.db import SYSTEM_USER_ID, AuditContext, configure_database, new_session, set_audit_context

ADMIN_URL = os.environ.get("GBCFMS_TEST_ADMIN_URL", "postgresql+psycopg://gbcfms:gbcfms@localhost:5432/postgres")
TEMPLATE_DB = "gbcfms_test_template"


def _admin_engine():
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def _db_url(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session", autouse=True)
def template_database():
    eng = _admin_engine()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{TEMPLATE_DB}"'))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.attributes["database_url"] = _db_url(TEMPLATE_DB)
    command.upgrade(cfg, "head")
    yield
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)'))
    eng.dispose()


@pytest.fixture()
def database(template_database):
    name = f"gbcfms_t_{uuid.uuid4().hex[:12]}"
    eng = _admin_engine()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE_DB}"'))
    configure_database(_db_url(name))
    yield _db_url(name)
    configure_database(None)
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


@pytest.fixture()
def session(database):
    s = new_session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def sys_ctx():
    return AuditContext(user_id=SYSTEM_USER_ID, reason="test")


def begin_write(session, user_id=SYSTEM_USER_ID, reason="test"):
    """يبدأ معاملة كتابة بسياق تدقيق."""
    set_audit_context(session, AuditContext(user_id=user_id, reason=reason))
