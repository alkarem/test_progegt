"""الحجوزات والارتباطات (المرحلة 8)."""
import pytest
from sqlalchemy import select

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.ledger_helpers import D, budget_doc, build_world, position

API = "/api/v1"


@pytest.fixture()
def team(user_factory):
    return {r: user_factory(r.lower(), r) for r in
            ("DATA_ENTRY", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER")}


def item(code):
    with new_session() as s:
        return str(s.scalar(select(BudgetItem.id).where(BudgetItem.code == code)))


def create(client, h, w, ctype, amount, code="2/18", **kw):
    r = client.post(f"{API}/commitments", headers=h, json={
        "fiscal_year_id": str(w.fy_id), "commitment_type": ctype, "commitment_date": "2026-04-01",
        "entity_id": str(w.entity_id), "item_id": item(code), "amount": amount,
        "description": "صيانة المولد الكهربائي"} | kw)
    assert r.status_code == 201, r.text
    return r.json()


def run(client, team, cid, pr=False):
    r = client.post(f"{API}/documents/commitment/{cid}/submit", headers=team["DATA_ENTRY"], json={})
    assert r.status_code == 200, r.text
    roles = ("BUDGET_CONTROLLER", "SUPERVISOR") if pr else ("FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR",
                                                            "APPROVER")
    for role in roles:
        r = client.post(f"{API}/documents/commitment/{cid}/approve", headers=team[role], json={})
        if r.status_code != 200:
            return r
    return r


def get(client, h, cid):
    return client.get(f"{API}/commitments/{cid}", headers=h).json()


def test_purchase_request_to_purchase_order_flow(client, team):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    pr = create(client, team["DATA_ENTRY"], w, "PURCHASE_REQUEST", "30000")
    assert pr["commitment_no"] == "PR-2026-00001"
    client.post(f"{API}/documents/commitment/{pr['id']}/submit", headers=team["DATA_ENTRY"], json={})
    assert get(client, team["DATA_ENTRY"], pr["id"])["commitment_status"] == "PENDING_APPROVAL"
    for role in ("BUDGET_CONTROLLER", "SUPERVISOR"):   # مسار طلب الشراء المختصر
        assert client.post(f"{API}/documents/commitment/{pr['id']}/approve", headers=team[role],
                           json={}).status_code == 200
    pr = get(client, team["DATA_ENTRY"], pr["id"])
    assert (pr["status"], pr["commitment_status"], pr["outstanding"]) == ("POSTED", "APPROVED", "30000.000")
    assert position(w, "2/18").available == D("70000")

    po = client.post(f"{API}/commitments/{pr['id']}/convert", headers=team["DATA_ENTRY"], json={
        "commitment_type": "PURCHASE_ORDER", "commitment_date": "2026-04-10", "amount": "28000",
        "description": "أمر شراء قطع غيار المولد"}).json()
    assert po["parent_id"] == pr["id"] and po["commitment_no"] == "PO-2026-00001"
    r = client.post(f"{API}/commitments/{pr['id']}/convert", headers=team["DATA_ENTRY"], json={
        "commitment_type": "PURCHASE_ORDER", "commitment_date": "2026-04-10", "amount": "1", "description": "مكرر"})
    assert r.status_code == 409   # طلب الشراء لا يُحوَّل مرتين
    assert run(client, team, po["id"]).json()["doc_status"] == "POSTED"
    p = position(w, "2/18")
    assert (p.reservation, p.commitment, p.available) == (D("0"), D("28000"), D("72000"))
    assert get(client, team["DATA_ENTRY"], pr["id"])["commitment_status"] == "CLOSED"
    assert get(client, team["DATA_ENTRY"], po["id"])["outstanding"] == "28000.000"
    mv = client.get(f"{API}/commitments/{pr['id']}/movements", headers=team["DATA_ENTRY"]).json()
    assert [m["txn_type"] for m in mv] == ["PRE_COMMITMENT", "RESERVATION_RELEASE"]


def test_commitment_exceeding_available_is_blocked(client, team):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    c = create(client, team["DATA_ENTRY"], w, "CONTRACT", "10000.001")
    r = run(client, team, c["id"])
    assert r.status_code == 409 and r.json()["details"]["shortfall"] == "0.001"


def test_commitment_reduces_available_before_expenditure(client, team):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "50000"})
    c = create(client, team["DATA_ENTRY"], w, "OBLIGATION", "20000")
    run(client, team, c["id"])
    p = position(w, "2/18")
    assert (p.book_balance, p.available) == (D("50000"), D("30000"))   # يظهر قبل تحوله لمصروف


def test_convert_requires_posted_request_on_same_line(client, team):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "5000", "2/16": "5000"})
    pr = create(client, team["DATA_ENTRY"], w, "PURCHASE_REQUEST", "100")
    r = client.post(f"{API}/commitments/{pr['id']}/convert", headers=team["DATA_ENTRY"], json={
        "commitment_type": "PURCHASE_ORDER", "commitment_date": "2026-04-10", "amount": "100", "description": "أمر"})
    assert r.status_code == 409 and r.json()["code"] == "PARENT_NOT_OPEN"
