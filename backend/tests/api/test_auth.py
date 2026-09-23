"""اختبارات المصادقة والجلسات والأمن (المرحلة 2)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text, update

from app.core.db import new_session
from app.modules.users.models import RefreshToken
from tests.conftest import PASSWORD, create_user, login

API = "/api/v1"


def test_login_success_returns_access_token_and_httponly_cookie(client):
    create_user("admin1", ["SYSTEM_ADMIN"])
    r = client.post(f"{API}/auth/login", json={"username": "admin1", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"
    cookie = r.headers["set-cookie"]
    assert "gbcfms_refresh=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["username"] == "admin1"
    assert "users.manage" in me.json()["permissions"]


def test_login_is_case_insensitive_on_username(client):
    create_user("Ahmed", ["AUDITOR"])
    assert client.post(f"{API}/auth/login", json={"username": "ahmed", "password": PASSWORD}).status_code == 200


def test_wrong_password_and_unknown_user_give_same_error(client):
    create_user("user1", ["AUDITOR"])
    a = client.post(f"{API}/auth/login", json={"username": "user1", "password": "wrong"})
    b = client.post(f"{API}/auth/login", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json()["title"] == b.json()["title"]


def test_account_locks_after_repeated_failures(client):
    create_user("user2", ["AUDITOR"])
    for _ in range(5):
        client.post(f"{API}/auth/login", json={"username": "user2", "password": "wrong"})
    r = client.post(f"{API}/auth/login", json={"username": "user2", "password": PASSWORD})
    assert r.status_code == 423
    assert r.json()["code"] == "ACCOUNT_LOCKED"


def test_login_attempts_are_audited(client):
    create_user("user3", ["AUDITOR"])
    client.post(f"{API}/auth/login", json={"username": "user3", "password": "bad"})
    client.post(f"{API}/auth/login", json={"username": "user3", "password": PASSWORD})
    with new_session() as s:
        actions = s.scalars(text("SELECT action FROM audit_log WHERE action LIKE 'LOGIN%' ORDER BY id")).all()
    assert actions == ["LOGIN_FAILED", "LOGIN_SUCCESS"]


def test_rate_limit_on_login(client):
    for _ in range(10):
        client.post(f"{API}/auth/login", json={"username": "x", "password": "y"})
    r = client.post(f"{API}/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 429


def test_protected_endpoint_requires_token(client):
    assert client.get(f"{API}/auth/me").status_code == 401
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_forged_and_none_alg_tokens_rejected(client):
    import jwt
    uid = create_user("user4", ["SYSTEM_ADMIN"])
    now = datetime.now(UTC)
    claims = {"sub": str(uid), "sid": str(uid), "av": 1, "iat": now, "exp": now + timedelta(minutes=5),
              "typ": "access"}
    forged = jwt.encode(claims, "wrong-secret-wrong-secret-wrong-secret!!", algorithm="HS256")
    none_alg = jwt.encode(claims, None, algorithm="none")
    for tok in (forged, none_alg):
        assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_must_change_password_blocks_business_endpoints(client):
    create_user("newbie", ["SYSTEM_ADMIN"], must_change=True)
    h = login(client, "newbie")
    r = client.get(f"{API}/users", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    r = client.post(f"{API}/auth/change-password", headers=h,
                    json={"current_password": PASSWORD, "new_password": "Brand-New-Pass-99"})
    assert r.status_code == 200
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"{API}/users", headers=h2).status_code == 200
    # الرمز القديم أُبطل
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_weak_password_rejected(client):
    create_user("user5", ["AUDITOR"])
    h = login(client, "user5")
    r = client.post(f"{API}/auth/change-password", headers=h,
                    json={"current_password": PASSWORD, "new_password": "short1"})
    assert r.status_code == 422 and r.json()["code"] == "WEAK_PASSWORD"


def test_refresh_rotates_token_and_reuse_revokes_family(client):
    create_user("user6", ["AUDITOR"])
    login(client, "user6")
    first = client.cookies.get("gbcfms_refresh")
    r = client.post(f"{API}/auth/refresh")
    assert r.status_code == 200
    second = client.cookies.get("gbcfms_refresh")
    assert first != second
    access = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"{API}/auth/me", headers=access).status_code == 200
    # إعادة استخدام الرمز الأول (سُرق مثلًا)
    client.cookies.set("gbcfms_refresh", first, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 401
    # العائلة كلها أُلغيت: الرمز الثاني والوصول انتهيا
    client.cookies.set("gbcfms_refresh", second, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 401
    assert client.get(f"{API}/auth/me", headers=access).status_code == 401
    with new_session() as s:
        assert s.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'REFRESH_REUSE_DETECTED'")) == 1


def test_refresh_idle_timeout(client):
    create_user("user7", ["AUDITOR"])
    login(client, "user7")
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        s.execute(update(RefreshToken).values(issued_at=datetime.now(UTC) - timedelta(hours=1)))
        s.commit()
    r = client.post(f"{API}/auth/refresh")
    assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED"


def test_refresh_rejects_foreign_origin(client):
    create_user("user8", ["AUDITOR"])
    login(client, "user8")
    r = client.post(f"{API}/auth/refresh", headers={"Origin": "https://evil.example"})
    assert r.status_code == 401 and r.json()["code"] == "BAD_ORIGIN"


def test_logout_invalidates_access_token(client):
    create_user("user9", ["AUDITOR"])
    h = login(client, "user9")
    assert client.post(f"{API}/auth/logout", headers=h).status_code == 204
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_sessions_list_and_revoke(client):
    create_user("user10", ["AUDITOR"])
    h1 = login(client, "user10")
    h2 = login(client, "user10")
    sessions = client.get(f"{API}/auth/sessions", headers=h2).json()
    assert len(sessions) == 2
    other = next(s for s in sessions if not s["current"])
    assert client.delete(f"{API}/auth/sessions/{other['id']}", headers=h2).status_code == 204
    assert client.get(f"{API}/auth/me", headers=h1).status_code == 401
    assert client.get(f"{API}/auth/me", headers=h2).status_code == 200


def test_security_headers_present(client):
    r = client.get(f"{API}/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
