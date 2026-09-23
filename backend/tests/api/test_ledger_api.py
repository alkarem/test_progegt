"""واجهات الموقف والتسلسل الزمني ومستندات الميزانية ومنح الاستثناء (المرحلة 4)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.ledger_helpers import actual, budget_doc, build_world, post, spec, transfer

API = "/api/v1"


def _ids(code):
    with new_session() as s:
        return s.scalar(select(BudgetItem.id).where(BudgetItem.code == code))


def test_budget_position_lists_all_postable_items_with_components(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "180000", "2/16": "40000"})
    transfer(w, "2/18", "2/16", "80000")
    actual(w, "2/16", "110000")
    h = user_factory("viewer", "REPORT_VIEWER")
    rows = client.get(f"{API}/budget-position", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert len(rows) == 29
    by = {r["item_code"]: r for r in rows}
    p16 = by["2/16"]["position"]
    assert p16["transfer_in"] == "80000.000" and p16["available"] == "10000.000"
    assert p16["actual_rate"] == "91.67" and by["2/16"]["level"] == "WARNING"
    assert by["2/1"]["budget_line_id"] is None and by["2/1"]["level"] == "NO_BUDGET"


def test_line_timeline_running_available_and_as_of(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    post(w, spec(w, "2/18", "COMMITMENT", "COMMITMENT", "300"))
    actual(w, "2/18", "200", commitment_liquidation="200")
    h = user_factory("viewer", "REPORT_VIEWER")
    tl = client.get(f"{API}/budget-lines/{w.lines['2/18']}/timeline", headers=h).json()
    assert [r["txn_type"] for r in tl] == ["ORIGINAL_BUDGET", "COMMITMENT", "ACTUAL_EXPENDITURE",
                                           "COMMITMENT_LIQUIDATION"]
    assert [r["available_after"] for r in tl] == ["1000.000", "700.000", "500.000", "700.000"]
    pos = client.get(f"{API}/budget-lines/{w.lines['2/18']}", headers=h, params={"as_of": "2026-01-31"}).json()
    assert pos["position"]["available"] == "1000.000"


def test_check_endpoint_reports_shortfall(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "5000"})
    h = user_factory("clerk", "DATA_ENTRY")
    r = client.post(f"{API}/budget-lines/{w.lines['2/18']}/check", headers=h, json={"amount": "8000"}).json()
    assert r == {"ok": False, "available": "5000.000", "requested": "8000.000", "shortfall": "3000.000",
                 "message": "لا يوجد اعتماد متاح كافٍ لهذه العملية."}


def test_amount_as_float_is_rejected(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    h = user_factory("clerk", "DATA_ENTRY")
    r = client.post(f"{API}/budget-lines/{w.lines['2/18']}/check", headers=h, json={"amount": 0.1})
    assert r.status_code == 422
    r = client.post(f"{API}/budget-lines/{w.lines['2/18']}/check", headers=h, json={"amount": "1.0001"})
    assert r.status_code == 422


def test_budget_document_draft_lifecycle_and_optimistic_lock(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    h = user_factory("clerk", "DATA_ENTRY")
    body = {"fiscal_year_id": str(w.fy_id), "kind": "ORIGINAL_BUDGET", "doc_date": "2026-01-01",
            "description": "الاعتماد الأصلي للباب الثاني", "lines": [
                {"entity_id": str(w.entity_id), "item_id": str(_ids("2/18")), "amount": "180000"},
                {"entity_id": str(w.entity_id), "item_id": str(_ids("2/16")), "amount": "40000.5"}]}
    d = client.post(f"{API}/budget-documents", headers=h, json=body).json()
    assert d["doc_no"] == "BUD-2026-00001" and d["status"] == "DRAFT" and d["total"] == "220000.500"
    r = client.patch(f"{API}/budget-documents/{d['id']}", headers=h | {"If-Match": "99"},
                     json={"description": "تعديل"})
    assert r.status_code == 412 and r.json()["code"] == "VERSION_CONFLICT"
    r = client.patch(f"{API}/budget-documents/{d['id']}", headers=h | {"If-Match": "1"},
                     json={"lines": [{"entity_id": str(w.entity_id), "item_id": str(_ids("2/18")),
                                      "amount": "1"}]})
    assert r.status_code == 200 and r.json()["total"] == "1.000" and r.json()["row_version"] == 2
    other = user_factory("clerk2", "DATA_ENTRY")
    r = client.patch(f"{API}/budget-documents/{d['id']}", headers=other, json={"description": "ليس لي"})
    assert r.status_code == 403


def test_budget_document_validation(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    h = user_factory("clerk", "DATA_ENTRY")
    line = {"entity_id": str(w.entity_id), "item_id": str(_ids("2/18")), "amount": "5"}
    base = {"fiscal_year_id": str(w.fy_id), "kind": "BUDGET_INCREASE", "doc_date": "2026-01-01",
            "description": "تعزيز"}
    assert client.post(f"{API}/budget-documents", headers=h, json=base | {"lines": [line, line]}).json()["code"] \
        == "DUPLICATE_LINE"
    assert client.post(f"{API}/budget-documents", headers=h,
                       json=base | {"doc_date": "2027-01-01", "lines": [line]}).json()["code"] == "DATE_OUTSIDE_YEAR"
    assert client.post(f"{API}/budget-documents", headers=h,
                       json=base | {"lines": [line | {"amount": "0"}]}).status_code == 422
    viewer = user_factory("viewer", "REPORT_VIEWER")
    assert client.post(f"{API}/budget-documents", headers=viewer, json=base | {"lines": [line]}).status_code == 403


def test_override_grant_api_rules(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    approver = user_factory("approver", "APPROVER")
    user_factory("approver2", "APPROVER")
    user_factory("clerk", "DATA_ENTRY")
    users = {u["username"]: u["id"] for u in
             client.get(f"{API}/users", headers=user_factory("admin", "SYSTEM_ADMIN")).json()["items"]}
    now = datetime.now(UTC)
    body = {"budget_line_id": str(w.lines["2/18"]), "max_amount": "1000",
            "valid_from": now.isoformat(), "valid_to": (now + timedelta(days=1)).isoformat(),
            "reason": "صيانة طارئة للمولد"}
    me = client.get(f"{API}/auth/me", headers=approver).json()["id"]
    assert client.post(f"{API}/override-grants", headers=approver, json=body | {"user_id": me}).json()["code"] \
        == "SELF_GRANT"
    assert client.post(f"{API}/override-grants", headers=approver,
                       json=body | {"user_id": users["clerk"]}).json()["code"] == "GRANTEE_NOT_APPROVER"
    r = client.post(f"{API}/override-grants", headers=approver, json=body | {"user_id": users["approver2"]})
    assert r.status_code == 201 and r.json()["used_amount"] == "0.000"
    clerk = user_factory("clerk3", "DATA_ENTRY")
    assert client.post(f"{API}/override-grants", headers=clerk, json=body | {"user_id": users["approver2"]}
                       ).status_code == 403
    rv = client.post(f"{API}/override-grants/{r.json()['id']}/revoke", headers=approver)
    assert rv.json()["revoked_at"] is not None


def test_reconcile_endpoint(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    h = user_factory("ctrl", "BUDGET_CONTROLLER")
    assert client.post(f"{API}/ledger/reconcile", headers=h).json()["ok"] is True
    assert client.post(f"{API}/ledger/reconcile", headers=user_factory("clerk", "DATA_ENTRY")).status_code == 403


def test_ledger_entries_respect_item_scope(client, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000", "2/16": "500"})
    h = user_factory("scoped", "AUDITOR", scopes={"ITEM": [_ids("2/16")]})
    rows = client.get(f"{API}/ledger-entries", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert rows["total"] == 1 and rows["items"][0]["item_code"] == "2/16"
    r = client.get(f"{API}/budget-lines/{w.lines['2/18']}", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "OUT_OF_SCOPE"
