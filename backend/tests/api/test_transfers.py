"""المناقلات (المرحلة 7)."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.ledger_helpers import D, actual, budget_doc, build_world, position

API = "/api/v1"


@pytest.fixture()
def team(user_factory):
    return {r: user_factory(r.lower(), r) for r in
            ("DATA_ENTRY", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER")}


def ids():
    with new_session() as s:
        return {c: str(i) for c, i in s.execute(select(BudgetItem.code, BudgetItem.id))}


def body(w, lines, **kw):
    i = ids()
    return {"fiscal_year_id": str(w.fy_id), "entity_id": str(w.entity_id), "transfer_date": "2026-03-05",
            "reason": "تعزيز بند التجهيزات", "approval_no": "ق-12/2026",
            "lines": [{"from_item_id": i[a], "to_item_id": i[b], "amount": amt} for a, b, amt in lines]} | kw


def run(client, team, tid):
    client.post(f"{API}/documents/transfer/{tid}/submit", headers=team["DATA_ENTRY"], json={})
    r = None
    for role in ("FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER"):
        r = client.post(f"{API}/documents/transfer/{tid}/approve", headers=team[role], json={})
        if r.status_code != 200:
            return r
    return r


def test_multi_line_transfer_posts_balanced_pairs(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/6", "2/7", "2/16"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "20000", "2/7": "6000"})
    t = client.post(f"{API}/transfers", headers=team["DATA_ENTRY"],
                    json=body(w, [("2/6", "2/16", "20000"), ("2/7", "2/16", "6000")])).json()
    assert t["transfer_no"] == "TRF-2026-00001" and t["total"] == "26000.000"
    assert t["lines"][0]["source"]["available_now"] == "20000.000"
    r = run(client, team, t["id"])
    assert r.json()["doc_status"] == "POSTED"
    assert position(w, "2/16").transfer_in == D("26000") and position(w, "2/6").available == D("0")
    with new_session() as s:
        rows = s.execute(text("SELECT transfer_group_id, sum(CASE WHEN component='TRANSFER_OUT' THEN amount END),"
                              " sum(CASE WHEN component='TRANSFER_IN' THEN amount END) FROM ledger_entries"
                              " WHERE source_type='transfer' GROUP BY 1")).all()
    assert len(rows) == 2 and all(o == i for _, o, i in rows)
    assert client.get(f"{API}/transfers/{t['id']}", headers=team["APPROVER"]).json()["approved_by"] is not None


def test_transfer_exceeding_available_blocked_at_budget_control(client, team):
    """حالة DQ-06 من ملف Excel: نقل كامل التفويض رغم وجود مصروف سابق."""
    w = build_world(basis="APPROPRIATION", items=("2/6", "2/16"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "20000"})
    actual(w, "2/6", "16994")
    t = client.post(f"{API}/transfers", headers=team["DATA_ENTRY"], json=body(w, [("2/6", "2/16", "20000")])).json()
    r = run(client, team, t["id"])
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_BUDGET"
    assert r.json()["details"]["available"] == "3,006.000" and r.json()["details"]["shortfall"] == "16,994.000"


def test_same_item_and_validation(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/6",))
    r = client.post(f"{API}/transfers", headers=team["DATA_ENTRY"], json=body(w, [("2/6", "2/6", "1")]))
    assert r.status_code == 422 and r.json()["code"] == "SAME_LINE"
    r = client.post(f"{API}/transfers", headers=team["DATA_ENTRY"], json=body(w, [("2/6", "2/16", "-5")]))
    assert r.status_code == 422


def test_transfer_entries_cannot_be_altered_individually(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/6", "2/16"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "100"})
    t = client.post(f"{API}/transfers", headers=team["DATA_ENTRY"], json=body(w, [("2/6", "2/16", "100")])).json()
    run(client, team, t["id"])
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError):
            s.execute(text("DELETE FROM ledger_entries WHERE source_type='transfer' AND component='TRANSFER_IN'"))
