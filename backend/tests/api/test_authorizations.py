"""التفويضات والمرفقات (المرحلة 6)."""
import io
import zipfile

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.ledger_helpers import build_world

API = "/api/v1"
AUTH_479 = {"2/1": "16000", "2/2": "1500", "2/3": "500", "2/4": "5000", "2/5": "500", "2/6": "20000", "2/7": "6000",
            "2/8": "1000", "2/10": "2000", "2/11": "500", "2/16": "40000", "2/17": "4000", "2/18": "180000",
            "2/21": "5000", "2/27": "17000", "2/28": "1000"}
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture()
def team(user_factory):
    return {r: user_factory(r.lower(), r) for r in
            ("DATA_ENTRY", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER")}


def items():
    with new_session() as s:
        return {c: str(i) for c, i in s.execute(select(BudgetItem.code, BudgetItem.id))}


def auth_body(w, allocations, amount="300000", **kw):
    ids = items()
    return {"fiscal_year_id": str(w.fy_id), "auth_no": "479", "auth_type": "FINANCIAL", "auth_date": "2023-01-15",
            "entity_id": str(w.entity_id), "amount": amount, "purpose": "تفويض مالي للباب الثاني",
            "allocations": [{"item_id": ids[c], "amount": a} for c, a in allocations.items()]} | kw


def approve_all(client, team, source_type, doc_id, steps=("FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "APPROVER")):
    r = client.post(f"{API}/documents/{source_type}/{doc_id}/submit", headers=team["DATA_ENTRY"], json={})
    assert r.status_code == 200, r.text
    for role in steps:
        r = client.post(f"{API}/documents/{source_type}/{doc_id}/approve", headers=team[role], json={})
        if r.status_code != 200:
            return r
    return r


def test_authorization_479_distribution_posts_allocations(client, team):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    r = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(w, AUTH_479))
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["allocated"], a["unallocated"], len(a["allocations"])) == ("300000.000", "0.000", 16)
    r = approve_all(client, team, "authorization", a["id"])
    assert r.json()["doc_status"] == "POSTED"
    pos = {x["item_code"]: x["position"] for x in client.get(
        f"{API}/budget-position", headers=team["APPROVER"], params={"fiscal_year_id": str(w.fy_id)}).json()}
    assert pos["2/18"]["allocation"] == "180000.000" and pos["2/18"]["available"] == "180000.000"
    assert sum(float(p["allocation"]) for p in pos.values()) == 300000
    entries = client.get(f"{API}/ledger-entries", headers=team["APPROVER"],
                         params={"source_type": "authorization", "page_size": 100}).json()
    assert entries["total"] == 16 and {e["txn_type"] for e in entries["items"]} == {"BUDGET_ALLOCATION"}
    assert {e["document_no"] for e in entries["items"]} == {"479"}


def test_duplicate_authorization_number(client, team):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(w, {"2/18": "10"}))
    r = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(w, {"2/18": "10"}))
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_AUTHORIZATION"
    r = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"],
                    json=auth_body(w, {"2/18": "10"}, auth_type="DEPARTMENTAL"))
    assert r.status_code == 201   # الرقم نفسه مسموح لنوع آخر (مصلحي)


def test_partial_distribution_allowed_and_reported(client, team):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    a = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"],
                    json=auth_body(w, {"2/18": "1000"}, amount="5000")).json()
    assert a["unallocated"] == "4000.000"
    assert approve_all(client, team, "authorization", a["id"]).json()["doc_status"] == "POSTED"


def test_over_allocation_requires_reason_and_special_permission(client, team, user_factory):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    r = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"],
                    json=auth_body(w, {"2/18": "6000"}, amount="5000"))
    assert r.status_code == 422 and r.json()["code"] == "OVER_ALLOCATION_REASON_REQUIRED"
    a = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(
        w, {"2/18": "6000"}, amount="5000", over_allocation_reason="فرق تقريب في كتاب الوزارة")).json()
    # معتمد بصلاحية خاصة (دور APPROVER يملك authorizations.over_allocate)
    assert approve_all(client, team, "authorization", a["id"]).json()["doc_status"] == "POSTED"
    with new_session() as s:
        assert s.scalar(text("SELECT over_allocation_approved_by IS NOT NULL FROM authorizations WHERE id=:i"),
                        {"i": a["id"]})


def test_database_blocks_over_allocation_without_approval(client, team):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    a = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(
        w, {"2/18": "6000"}, amount="5000", over_allocation_reason="سبب")).json()
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError, match="يتجاوز قيمة التفويض"):
            s.execute(text("UPDATE authorizations SET status = 'POSTED' WHERE id = :i"), {"i": a["id"]})


def test_two_level_allocation_cannot_exceed_appropriation(client, team):
    from tests.ledger_helpers import budget_doc
    w = build_world(year=2026, basis="TWO_LEVEL")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    body = auth_body(w, {"2/18": "120000"}, amount="120000", auth_date="2026-02-01")
    a = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=body).json()
    r = approve_all(client, team, "authorization", a["id"])
    assert r.status_code == 409 and r.json()["code"] == "EXCEEDS_APPROPRIATION"


# --- المرفقات -----------------------------------------------------------------
def _xlsx(with_macro=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        if with_macro:
            z.writestr("xl/vbaProject.bin", b"\x00")
    return buf.getvalue()


def upload(client, h, name, data, **form):
    return client.post(f"{API}/attachments", headers=h, files={"file": (name, data)}, data=form)


def test_file_validation(client, team, settings_tmp_storage):
    h = team["DATA_ENTRY"]
    assert upload(client, h, "letter.pdf", PDF, category="AUTHORIZATION_LETTER").status_code == 201
    assert upload(client, h, "fake.pdf", b"MZ\x90\x00 executable").json()["code"] == "FILE_TYPE_NOT_ALLOWED"
    assert upload(client, h, "virus.exe", b"MZ").json()["code"] == "FILE_TYPE_NOT_ALLOWED"
    assert upload(client, h, "sheet.xlsx", _xlsx()).status_code == 201
    assert upload(client, h, "macro.xlsx", _xlsx(True)).json()["code"] == "MACRO_NOT_ALLOWED"
    assert upload(client, h, "doc.docx", _xlsx()).json()["code"] == "TYPE_MISMATCH"
    assert upload(client, h, "empty.pdf", b"").json()["code"] == "EMPTY_FILE"
    r = upload(client, h, "../../etc/passwd.pdf", PDF)
    assert r.status_code == 201 and r.json()["original_filename"] == "passwd.pdf"


def test_attachments_lock_when_document_posted(client, team, settings_tmp_storage):
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    a = client.post(f"{API}/authorizations", headers=team["DATA_ENTRY"], json=auth_body(w, {"2/18": "10"})).json()
    att = upload(client, team["DATA_ENTRY"], "letter.pdf", PDF, source_type="authorization",
                 source_id=a["id"]).json()
    listed = client.get(f"{API}/attachments", headers=team["FINANCIAL_REVIEWER"],
                        params={"source_type": "authorization", "source_id": a["id"]}).json()
    assert [x["id"] for x in listed] == [att["id"]]
    d = client.get(f"{API}/attachments/{att['id']}/download", headers=team["FINANCIAL_REVIEWER"])
    assert d.content == PDF and "attachment;" in d.headers["content-disposition"]
    approve_all(client, team, "authorization", a["id"])
    assert client.get(f"{API}/attachments/{att['id']}", headers=team["APPROVER"]).json()["is_locked"] is True
    r = client.delete(f"{API}/attachments/{att['id']}/links/authorization/{a['id']}", headers=team["DATA_ENTRY"])
    assert r.status_code == 409 and r.json()["code"] == "ATTACHMENT_LOCKED"
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError, match="مقفل"):
            s.execute(text("DELETE FROM attachments WHERE id = :i"), {"i": att["id"]})
    # يسمح بإضافة مرفق جديد لمستند مرحّل
    assert upload(client, team["DATA_ENTRY"], "extra.pdf", PDF, source_type="authorization",
                  source_id=a["id"]).status_code == 201


def test_tampered_file_detected_on_download(client, team, settings_tmp_storage):
    att = upload(client, team["DATA_ENTRY"], "x.pdf", PDF).json()
    for f in settings_tmp_storage.rglob("*.pdf"):
        f.write_bytes(PDF + b"tampered")
    r = client.get(f"{API}/attachments/{att['id']}/download", headers=team["DATA_ENTRY"])
    assert r.status_code == 409 and r.json()["code"] == "INTEGRITY_FAILURE"


def test_attachment_access_requires_document_visibility(client, team, user_factory, settings_tmp_storage):
    att = upload(client, team["DATA_ENTRY"], "x.pdf", PDF).json()
    other = user_factory("stranger", "REPORT_VIEWER")
    assert client.get(f"{API}/attachments/{att['id']}", headers=other).status_code == 403
