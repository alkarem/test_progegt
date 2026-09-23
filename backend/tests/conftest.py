"""بنية الاختبار: PostgreSQL حقيقي. قاعدة قالب واحدة بعد الترحيلات، ونسخة جديدة منها لكل اختبار."""
import contextlib
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


@contextlib.contextmanager
def fresh_database():
    """قاعدة جديدة من القالب (للاختبارات التي تحتاج أكثر من قاعدة، مثل اختبارات الخصائص)."""
    name = f"gbcfms_t_{uuid.uuid4().hex[:12]}"
    eng = _admin_engine()
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE_DB}"'))
    configure_database(_db_url(name))
    try:
        yield _db_url(name)
    finally:
        configure_database(None)
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


@pytest.fixture()
def database(template_database):
    with fresh_database() as url:
        yield url


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


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
PASSWORD = "Str0ng-Passw0rd-2026"


@pytest.fixture()
def app(database):
    from app.core.ratelimit import login_limiter
    from app.main import create_app
    login_limiter.reset()
    with new_session() as s:
        from app.modules.users.service import sync_permissions_and_roles
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="test setup"))
        sync_permissions_and_roles(s)
        from app.modules.workflow.definitions import sync_workflow_definitions
        sync_workflow_definitions(s)
        s.commit()
    return create_app()


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def create_user(username: str, roles: list[str], password: str = PASSWORD, must_change: bool = False,
                scopes: dict | None = None):
    from app.core.security import hash_password
    from app.modules.users.models import User
    from app.modules.users.service import set_roles, set_scopes
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="test user"))
        u = User(username=username, full_name=f"مستخدم {username}", password_hash=hash_password(password),
                 must_change_password=must_change)
        s.add(u)
        s.flush()
        set_roles(s, u, roles)
        if scopes:
            set_scopes(s, u, scopes)
        s.commit()
        return u.id


def login(client, username: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def user_factory(app):
    """ينشئ مستخدمًا بدور ويعيد ترويسة الدخول."""
    def make(username: str, *roles: str, **kw) -> dict:
        from fastapi.testclient import TestClient
        create_user(username, list(roles), **kw)
        with TestClient(app) as c:
            return login(c, username)
    return make


def seed_reference():
    from app.modules.catalog.seed import seed_reference_data
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="test seed"))
        seed_reference_data(s)
        s.commit()
