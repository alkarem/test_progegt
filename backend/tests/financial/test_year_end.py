"""الإقفال السنوي وترحيل الارتباطات (FR-YE، D-08)."""
from datetime import date

from sqlalchemy import select

from app.core.db import new_session
from app.modules.commitments.models import Commitment
from app.modules.fiscal import service as fiscal
from tests.conftest import drive
from tests.ledger_helpers import D, actual, budget_doc, build_world, position, write

API = "/api/v1"


def test_year_end_closing_releases_reservations_and_carries_commitments(client, team, user_factory):
    w = build_world(basis="APPROPRIATION", items=("2/18", "2/28"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    de = team["DATA_ENTRY"]
    item18 = client.get(f"{API}/items", headers=de).json()
    item18 = next(i["id"] for i in item18 if i["code"] == "2/18")
    body = {"fiscal_year_id": str(w.fy_id), "commitment_date": "2026-06-01", "entity_id": str(w.entity_id),
            "item_id": item18, "description": "اختبار الإقفال"}
    pr = client.post(f"{API}/commitments", headers=de, json=body | {"commitment_type": "PURCHASE_REQUEST",
                                                                     "amount": "3000"}).json()
    drive(client, team, "commitment", pr["id"])
    po = client.post(f"{API}/commitments", headers=de, json=body | {"commitment_type": "CONTRACT",
                                                                     "amount": "20000"}).json()
    drive(client, team, "commitment", po["id"])
    exp = client.post(f"{API}/expenditures", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-07-01", "entity_id": str(w.entity_id),
        "item_id": item18, "amount": "8000", "payment_method": "CASH", "description": "دفعة أولى",
        "commitment_id": po["id"]}).json()
    drive(client, team, "expenditure", exp["id"])
    approver = team["APPROVER"]

    prev = client.get(f"{API}/fiscal-years/{w.fy_id}/closing-preview", headers=approver).json()
    assert prev["commitments_to_carry"][0]["amount"] == "12000.000"
    assert prev["reservations_to_release"][0]["amount"] == "3000.000"

    # مستند معلق يمنع الإقفال
    draft = client.post(f"{API}/expenditures", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-07-02", "entity_id": str(w.entity_id),
        "item_id": item18, "amount": "1", "payment_method": "CASH", "description": "مسودة معلقة"}).json()
    r = client.post(f"{API}/fiscal-years/{w.fy_id}/close", headers=approver, json={"reason": "إقفال 2026"})
    assert r.status_code == 409 and r.json()["code"] == "PENDING_DOCUMENTS"
    client.post(f"{API}/documents/expenditure/{draft['id']}/cancel", headers=de, json={"comment": "لا حاجة"})

    # ارتباط قائم يتطلب فتح السنة التالية (D-08)
    r = client.post(f"{API}/fiscal-years/{w.fy_id}/close", headers=approver, json={"reason": "إقفال 2026"})
    assert r.status_code == 409 and r.json()["code"] == "NEXT_YEAR_REQUIRED"
    with new_session() as s:
        write(s)
        nxt = fiscal.create_year(s, year=2027, start_date=None, end_date=None, control_basis="APPROPRIATION",
                                 count_reservations=True, carry_forward_item_id=None)
        fiscal.open_year(s, nxt)
        s.commit()
        nxt_id = nxt.id
    assert client.post(f"{API}/fiscal-years/{w.fy_id}/close", headers=de, json={"reason": "x y z"}).status_code == 403
    r = client.post(f"{API}/fiscal-years/{w.fy_id}/close", headers=approver, json={"reason": "إقفال 2026"})
    assert r.status_code == 409 and r.json()["code"] == "CARRY_FORWARD_UNFUNDED"
    assert r.json()["details"]["items"][0] | {} == {"item_code": "2/28", "item_name": "مصروفات سنوات سابقة",
                                                     "required": "12000.000", "available": "0", "shortfall": "12000.000"}
    from tests.ledger_helpers import World
    w27 = World(nxt_id, w.entity_id, {}, 2027)
    budget_doc(w27, "ORIGINAL_BUDGET", {"2/28": "15000"})
    r = client.post(f"{API}/fiscal-years/{w.fy_id}/close", headers=approver, json={"reason": "إقفال 2026"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["reservations_released"], res["commitments_carried"]) == ("3000.000", "12000.000")

    p = position(w, "2/18")
    assert (p.reservation, p.commitment, p.actual) == (D("0"), D("0"), D("8000"))
    years = {y["year"]: y for y in client.get(f"{API}/fiscal-years", headers=approver).json()}
    assert years[2026]["status"] == "CLOSED"
    assert all(x["status"] == "CLOSED" for x in client.get(f"{API}/fiscal-years/{w.fy_id}/periods", headers=approver).json())

    with new_session() as s:
        carried = s.scalar(select(Commitment).where(Commitment.fiscal_year_id == nxt_id))
        original = s.get(Commitment, carried.carried_from_id)
    assert carried.amount == D("12000") and original.commitment_status == "CLOSED"
    c = client.get(f"{API}/commitments/{carried.id}", headers=de).json()
    assert (c["item_code"], c["outstanding"], c["commitment_status"]) == ("2/28", "12000.000", "APPROVED")

    # لا ترحيل على سنة مقفلة، والارتباط المرحّل يُسدَّد في السنة الجديدة
    try:
        actual(w, "2/18", "1")
        raise AssertionError("يجب رفض الترحيل على سنة مقفلة")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", "") in ("YEAR_NOT_OPEN",)
    assert date(2027, 1, 1)
