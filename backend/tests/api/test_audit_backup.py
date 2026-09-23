"""سجل التدقيق والنسخ الاحتياطي والاستعادة وصحة النظام (المرحلة 13)."""
import base64
import os

import pytest
from sqlalchemy import text

from app.core.db import new_session
from tests.ledger_helpers import D, actual, budget_doc, build_world, position

API = "/api/v1"


@pytest.fixture()
def backup_key(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "backup_key", base64.b64encode(os.urandom(32)).decode())


def test_audit_log_browse_filter_cursor_and_record_history(client, user_factory):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    h = user_factory("aud", "AUDITOR")
    page = client.get(f"{API}/audit-log", headers=h, params={"limit": 5}).json()
    assert len(page["items"]) == 5 and page["next_before_id"] is not None
    nxt = client.get(f"{API}/audit-log", headers=h, params={"limit": 5, "before_id": page["next_before_id"]}).json()
    assert nxt["items"][0]["id"] < page["items"][-1]["id"] + 1
    le = client.get(f"{API}/audit-log", headers=h, params={"table_name": "ledger_entries"}).json()["items"]
    assert le and all(r["table_name"] == "ledger_entries" for r in le)
    hist = client.get(f"{API}/audit-log/record/budget_items/{le[0]['new_values']['budget_line_id']}", headers=h)
    assert hist.status_code == 200
    v = client.post(f"{API}/audit-log/verify-chain", headers=h).json()
    assert v["ok"] is True and v["checked"] > 10 and len(v["last_hash"]) == 64
    assert client.get(f"{API}/audit-log", headers=user_factory("clerk", "DATA_ENTRY")).status_code == 403


def test_backup_encrypt_verify_tamper_and_restore(client, user_factory, settings_tmp_storage, backup_key, database):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    admin = user_factory("admin", "SYSTEM_ADMIN")
    b = client.post(f"{API}/backup/run", headers=admin).json()
    assert b["status"] == "SUCCEEDED" and b["encrypted"] is True and b["file_name"].endswith(".bak.enc")
    v = client.post(f"{API}/backups/{b['id']}/verify", headers=admin).json()
    assert v["verification"]["ok"] is True and v["verification"]["toc_entries"] > 50

    actual(w, "2/18", "600")                                           # تغيير بعد النسخة
    assert position(w, "2/18").available == D("400")
    r = client.post(f"{API}/backups/{b['id']}/restore", headers=admin, json={"confirm": "wrong"})
    assert r.status_code == 422 and r.json()["code"] == "CONFIRMATION_MISMATCH"
    dbname = database.rsplit("/", 1)[-1]
    r = client.post(f"{API}/backups/{b['id']}/restore", headers=admin, json={"confirm": dbname})
    assert r.status_code == 200, r.text
    assert position(w, "2/18").available == D("1000")               # عادت الحالة
    kinds = {x["kind"] for x in client.get(f"{API}/backups", headers=user_factory("admin2", "SYSTEM_ADMIN")).json()}
    assert {"MANUAL", "PRE_RESTORE"} <= kinds                          # سجل النسخ بقي بعد الاستعادة
    with new_session() as s:
        assert s.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'RESTORE'")) == 1
        broken, _ = s.execute(text("SELECT * FROM audit_verify_chain()")).one()
    assert broken is None

    # العبث بالملف يُكتشف
    for f in settings_tmp_storage.rglob("*.bak.enc"):
        f.write_bytes(f.read_bytes() + b"x")
    v = client.post(f"{API}/backups/{b['id']}/verify", headers=user_factory("admin3", "SYSTEM_ADMIN")).json()
    assert v["verification"]["ok"] is False


def test_backup_settings_and_health_require_admin(client, user_factory, settings_tmp_storage):
    admin = user_factory("admin", "SYSTEM_ADMIN")
    r = client.put(f"{API}/backup/settings", headers=admin, json={"daily_time": "03:30", "retention_count": 7})
    assert r.json()["daily_time"] == "03:30" and r.json()["retention_count"] == 7
    assert client.put(f"{API}/backup/settings", headers=admin, json={"daily_time": "25:00"}).status_code == 422
    h = client.get(f"{API}/health/db", headers=admin).json()
    assert h["balances_reconciled"] is True and "database_size" in h
    approver = user_factory("approver", "APPROVER")
    assert client.post(f"{API}/backup/run", headers=approver).status_code == 403
