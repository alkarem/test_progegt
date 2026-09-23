"""إدارة المستخدمين والأدوار والنطاقات، ومصفوفة الصلاحيات (المرحلة 2)."""
import pytest
from sqlalchemy import text

from app.core.db import new_session
from app.core.permissions import ROLES
from tests.conftest import create_user, login

API = "/api/v1"


def test_admin_creates_user_with_roles(client, user_factory):
    h = user_factory("admin", "SYSTEM_ADMIN")
    r = client.post(f"{API}/users", headers=h, json={
        "username": "clerk", "full_name": "موظف إدخال", "password": "Entry-Pass-2026x", "roles": ["DATA_ENTRY"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["roles"] == ["DATA_ENTRY"] and body["must_change_password"] is True
    with new_session() as s:
        row = s.execute(text("SELECT user_id FROM audit_log WHERE table_name='users' AND action='INSERT' "
                             "ORDER BY id DESC LIMIT 1")).one()
    me = client.get(f"{API}/auth/me", headers=h).json()
    assert str(row.user_id) == me["id"]  # التدقيق يسجل من أنشأ


def test_duplicate_username_and_unknown_role(client, user_factory):
    h = user_factory("admin", "SYSTEM_ADMIN")
    ok = {"username": "dup", "full_name": "أ", "password": "Clerk-Pass-2026x", "roles": []}
    assert client.post(f"{API}/users", headers=h, json=ok | {"full_name": "أحمد"}).status_code == 201
    r = client.post(f"{API}/users", headers=h, json=ok | {"full_name": "أحمد"})
    assert r.status_code == 409
    r = client.post(f"{API}/users", headers=h, json=ok | {"username": "xx2", "full_name": "أحمد",
                                                            "roles": ["GOD"]})
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_ROLE"


def test_role_change_revokes_existing_tokens(client, user_factory):
    admin = user_factory("admin", "SYSTEM_ADMIN")
    uid = create_user("target", ["AUDITOR"])
    th = login(client, "target")
    assert client.put(f"{API}/users/{uid}/roles", headers=admin, json={"roles": ["REPORT_VIEWER"]}).status_code == 200
    r = client.get(f"{API}/auth/me", headers=th)
    assert r.status_code == 401 and r.json()["code"] == "SESSION_REVOKED"


def test_deactivated_user_cannot_login(client, user_factory):
    admin = user_factory("admin", "SYSTEM_ADMIN")
    uid = create_user("leaver", ["AUDITOR"])
    assert client.patch(f"{API}/users/{uid}", headers=admin, json={"is_active": False}).status_code == 200
    r = client.post(f"{API}/auth/login", json={"username": "leaver", "password": "Str0ng-Passw0rd-2026"})
    assert r.status_code == 401


def test_admin_cannot_deactivate_self(client, user_factory):
    admin = user_factory("admin", "SYSTEM_ADMIN")
    me = client.get(f"{API}/auth/me", headers=admin).json()
    r = client.patch(f"{API}/users/{me['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 422


def test_scopes_validation(client, user_factory):
    admin = user_factory("admin", "SYSTEM_ADMIN")
    uid = create_user("scoped", ["DATA_ENTRY"])
    import uuid
    r = client.put(f"{API}/users/{uid}/scopes", headers=admin, json={"scopes": {"ITEM": [str(uuid.uuid4())]}})
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_SCOPE_ID"
    r = client.put(f"{API}/users/{uid}/scopes", headers=admin, json={"scopes": {"PLANET": []}})
    assert r.status_code == 422


def test_system_admin_has_no_financial_approval_permissions():
    perms = ROLES["SYSTEM_ADMIN"][1]
    assert not any(p.endswith((".approve", ".supervise", ".control", ".review", ".create", ".submit"))
                   for p in perms if p.split(".")[0] in {"transfers", "expenditures", "commitments",
                                                          "authorizations", "budget_documents", "adjustments"})
    assert "budget.override_grant" not in perms


def test_auditor_is_read_only():
    writes = (".create", ".submit", ".review", ".control", ".supervise", ".approve", ".cancel", ".manage")
    assert not any(p.endswith(writes) for p in ROLES["AUDITOR"][1])


@pytest.mark.parametrize("role", ["DATA_ENTRY", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR",
                                  "APPROVER", "AUDITOR", "REPORT_VIEWER"])
def test_non_admin_roles_cannot_manage_users(client, user_factory, role):
    h = user_factory(f"u_{role.lower()}", role)
    r = client.post(f"{API}/users", headers=h, json={"username": "zz", "full_name": "زز",
                                                     "password": "Clerk-Pass-2026x", "roles": []})
    assert r.status_code == 403


def test_roles_and_permissions_listing(client, user_factory):
    h = user_factory("aud", "AUDITOR")
    roles = client.get(f"{API}/roles", headers=h).json()
    assert {r["code"] for r in roles} == set(ROLES)
    perms = client.get(f"{API}/permissions", headers=h).json()
    assert any(p["code"] == "transfers.approve" for p in perms)
