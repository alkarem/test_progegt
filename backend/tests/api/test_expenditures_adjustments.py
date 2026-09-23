"""المصروف الفعلي والتسويات والقيود العكسية (المرحلة 9)، ومعيار النجاح (البند 49)."""
from sqlalchemy import select

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.conftest import drive
from tests.ledger_helpers import D, budget_doc, build_world, position

API = "/api/v1"
PDF = b"%PDF-1.4\n%%EOF"


def ids():
    with new_session() as s:
        return {c: str(i) for c, i in s.execute(select(BudgetItem.code, BudgetItem.id))}


def post_json(client, h, path, body, status=201):
    r = client.post(f"{API}{path}", headers=h, json=body)
    assert r.status_code == status, r.text
    return r.json()


def supplier(client, h, name):
    return post_json(client, h, "/suppliers", {"name": name, "allow_similar": True})["id"]


def expenditure(client, h, w, code, amount, **kw):
    i = ids()
    body = {"fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-05-10", "entity_id": str(w.entity_id),
            "item_id": i[code], "amount": amount, "payment_method": "BANK_TRANSFER",
            "description": "صرف مستحقات"} | kw
    return post_json(client, h, "/expenditures", body)


def test_success_criterion_full_cycle_without_excel(client, team, settings_tmp_storage):
    """البند 49: تفويض ← توزيع ← مناقلة ← ارتباط ← مصروف ← تسوية ← رصيد ← تقرير،
    مع معرفة: كم كان الاعتماد، وكم أُضيف، ونُقل، والتُزم به، وصُرف، وتبقى، ومن ومتى وبأي مستند."""
    w = build_world(basis="TWO_LEVEL", items=("2/16", "2/18"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "200000", "2/16": "50000"})
    i, de = ids(), team["DATA_ENTRY"]

    # 1) تفويض وتوزيعه
    auth = post_json(client, de, "/authorizations", {
        "fiscal_year_id": str(w.fy_id), "auth_no": "479", "auth_type": "FINANCIAL", "auth_date": "2026-01-20",
        "entity_id": str(w.entity_id), "amount": "150000", "purpose": "تفويض الربع الأول",
        "allocations": [{"item_id": i["2/18"], "amount": "120000"}, {"item_id": i["2/16"], "amount": "30000"}]})
    client.post(f"{API}/attachments", headers=de, files={"file": ("auth479.pdf", PDF)},
                data={"source_type": "authorization", "source_id": auth["id"], "category": "AUTHORIZATION_LETTER"})
    assert drive(client, team, "authorization", auth["id"]).json()["doc_status"] == "POSTED"

    # 2) مناقلة
    trf = post_json(client, de, "/transfers", {
        "fiscal_year_id": str(w.fy_id), "entity_id": str(w.entity_id), "transfer_date": "2026-02-05",
        "reason": "تعزيز التجهيزات", "approval_no": "ق-7",
        "lines": [{"from_item_id": i["2/18"], "to_item_id": i["2/16"], "amount": "20000"}]})
    assert drive(client, team, "transfer", trf["id"]).json()["doc_status"] == "POSTED"

    # 3) ارتباط (عقد) على 2/16
    sup = supplier(client, de, "محلات عيسي شحات وأبناؤه")
    con = post_json(client, de, "/commitments", {
        "fiscal_year_id": str(w.fy_id), "commitment_type": "CONTRACT", "commitment_date": "2026-03-01",
        "entity_id": str(w.entity_id), "item_id": i["2/16"], "amount": "40000", "supplier_id": sup,
        "description": "عقد توريد أثاث"})
    assert drive(client, team, "commitment", con["id"]).json()["doc_status"] == "POSTED"
    assert position(w, "2/16").available == D("10000")

    # 4) مصروفان على العقد: 25,000 ثم 20,000 (15,000 من الارتباط + 5,000 جديد يُفحص)
    e1 = expenditure(client, de, w, "2/16", "25000", commitment_id=con["id"], supplier_id=sup,
                     payment_method="CHEQUE", cheque_no="000123", payment_order_no="أ ص-55")
    assert drive(client, team, "expenditure", e1["id"]).json()["doc_status"] == "POSTED"
    c = client.get(f"{API}/commitments/{con['id']}", headers=de).json()
    assert (c["commitment_status"], c["paid"], c["outstanding"]) == ("PARTIALLY_PAID", "25000.000", "15000.000")
    e2 = expenditure(client, de, w, "2/16", "20000", commitment_id=con["id"], supplier_id=sup)
    assert drive(client, team, "expenditure", e2["id"]).json()["doc_status"] == "POSTED"
    c = client.get(f"{API}/commitments/{con['id']}", headers=de).json()
    assert (c["commitment_status"], c["outstanding"]) == ("FULLY_PAID", "0.000")

    # 5) تسوية: أمر شراء على 2/18 يُصرف جزئيًا ثم يُلغى الباقي
    po = post_json(client, de, "/commitments", {
        "fiscal_year_id": str(w.fy_id), "commitment_type": "PURCHASE_ORDER", "commitment_date": "2026-03-10",
        "entity_id": str(w.entity_id), "item_id": i["2/18"], "amount": "10000", "description": "صيانة مكيفات"})
    drive(client, team, "commitment", po["id"])
    e3 = expenditure(client, de, w, "2/18", "6000", commitment_id=po["id"])
    drive(client, team, "expenditure", e3["id"])
    cancel = post_json(client, de, "/adjustments", {
        "fiscal_year_id": str(w.fy_id), "kind": "COMMITMENT_CANCELLATION", "adjustment_date": "2026-06-30",
        "reason": "اكتمال الأعمال بأقل من قيمة الأمر", "commitment_id": po["id"]})
    assert cancel["amount"] == "4000.000"
    assert drive(client, team, "adjustment", cancel["id"]).json()["doc_status"] == "POSTED"
    assert client.get(f"{API}/commitments/{po['id']}", headers=de).json()["commitment_status"] == "CLOSED"

    # 6) الرصيد: كل مكون واضح
    pos = {r["item_code"]: r["position"] for r in client.get(
        f"{API}/budget-position", headers=team["APPROVER"], params={"fiscal_year_id": str(w.fy_id)}).json()}
    p16, p18 = pos["2/16"], pos["2/18"]
    assert (p16["appropriation"], p16["allocation"], p16["transfer_in"], p16["actual"], p16["commitment"],
            p16["available"]) == ("50000.000", "30000.000", "20000.000", "45000.000", "0.000", "5000.000")
    assert (p18["allocation"], p18["transfer_out"], p18["actual"], p18["commitment"], p18["available"],
            p18["unallocated"]) == ("120000.000", "20000.000", "6000.000", "0.000", "94000.000", "80000.000")

    # 7) من فعل كل عملية ومتى وبأي مستند
    tl = client.get(f"{API}/budget-lines/{w.lines['2/16']}/timeline", headers=team["APPROVER"]).json()
    assert [r["txn_type"] for r in tl] == ["ORIGINAL_BUDGET", "BUDGET_ALLOCATION", "TRANSFER_IN", "COMMITMENT",
                                           "ACTUAL_EXPENDITURE", "COMMITMENT_LIQUIDATION", "ACTUAL_EXPENDITURE",
                                           "COMMITMENT_LIQUIDATION"]
    assert all(r["posted_by"] and r["posted_at"] and r["document_no"] for r in tl)
    approver_id = client.get(f"{API}/auth/me", headers=team["APPROVER"]).json()["id"]
    assert tl[1]["posted_by"] == approver_id and tl[1]["document_no"] == "479"
    hist = client.get(f"{API}/documents/authorization/{auth['id']}/history", headers=de).json()
    assert [h["action"] for h in hist] == ["SUBMIT", "APPROVE", "APPROVE", "POST"]
    atts = client.get(f"{API}/attachments", headers=de,
                      params={"source_type": "authorization", "source_id": auth["id"]}).json()
    assert atts[0]["original_filename"] == "auth479.pdf" and atts[0]["is_locked"] is True
    assert client.post(f"{API}/ledger/reconcile", headers=team["BUDGET_CONTROLLER"]).json()["ok"] is True


def test_expenditure_validations(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    de = team["DATA_ENTRY"]
    e = expenditure(client, de, w, "2/18", "100", document_no="1")
    r = client.post(f"{API}/expenditures", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-05-10", "entity_id": str(w.entity_id),
        "item_id": ids()["2/18"], "amount": "5", "payment_method": "CASH", "description": "مكرر", "document_no": "1"})
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_DOCUMENT_NO"
    r = client.post(f"{API}/expenditures", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-05-10", "entity_id": str(w.entity_id),
        "item_id": ids()["2/18"], "amount": "5", "payment_method": "CHEQUE", "description": "بدون شيك"})
    assert r.json()["code"] == "CHEQUE_NO_REQUIRED"
    # أمانات (D-07)
    dep = expenditure(client, de, w, "2/18", "2600", payment_method="DEPOSIT_ACCOUNT",
                      description="حساب الودائع والأمانات رقم (477-204)")
    assert dep["payment_method"] == "DEPOSIT_ACCOUNT"
    assert e["status"] == "DRAFT"


def test_similar_expenditure_warning(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    de = team["DATA_ENTRY"]
    sup = supplier(client, de, "شركة التاج للمقاولات")
    expenditure(client, de, w, "2/18", "34500", supplier_id=sup, document_no="A1")
    e2 = expenditure(client, de, w, "2/18", "34500", supplier_id=sup, document_no="A2",
                     expenditure_date="2026-05-12")
    assert e2["possible_duplicates"] == ["A1"]


def test_cannot_pay_purchase_request_directly(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    de = team["DATA_ENTRY"]
    pr = post_json(client, de, "/commitments", {
        "fiscal_year_id": str(w.fy_id), "commitment_type": "PURCHASE_REQUEST", "commitment_date": "2026-03-10",
        "entity_id": str(w.entity_id), "item_id": ids()["2/18"], "amount": "100", "description": "طلب"})
    drive(client, team, "commitment", pr["id"])
    r = client.post(f"{API}/expenditures", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-05-10", "entity_id": str(w.entity_id),
        "item_id": ids()["2/18"], "amount": "5", "payment_method": "CASH", "description": "x y",
        "commitment_id": pr["id"]})
    assert r.json()["code"] == "CANNOT_PAY_PURCHASE_REQUEST"


def test_reversal_restores_balance_and_commitment(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    de = team["DATA_ENTRY"]
    po = post_json(client, de, "/commitments", {
        "fiscal_year_id": str(w.fy_id), "commitment_type": "PURCHASE_ORDER", "commitment_date": "2026-03-10",
        "entity_id": str(w.entity_id), "item_id": ids()["2/18"], "amount": "4000", "description": "أمر"})
    drive(client, team, "commitment", po["id"])
    e = expenditure(client, de, w, "2/18", "4000", commitment_id=po["id"])
    drive(client, team, "expenditure", e["id"])
    assert position(w, "2/18").available == D("6000")
    # عكس الارتباط ممنوع وعليه مدفوعات
    r = client.post(f"{API}/reversals", headers=de, json={"source_type": "commitment", "source_id": po["id"],
                                                          "adjustment_date": "2026-06-01", "reason": "خطأ في الأمر"})
    assert r.json()["code"] == "COMMITMENT_HAS_PAYMENTS"
    rev = post_json(client, de, "/reversals", {"source_type": "expenditure", "source_id": e["id"],
                                               "adjustment_date": "2026-06-01", "reason": "صرف على بند خطأ"})
    r = client.post(f"{API}/reversals", headers=de, json={"source_type": "expenditure", "source_id": e["id"],
                                                          "adjustment_date": "2026-06-01", "reason": "مرة ثانية"})
    assert r.status_code == 409 and r.json()["code"] == "REVERSAL_EXISTS"
    assert drive(client, team, "adjustment", rev["id"]).json()["doc_status"] == "POSTED"
    assert client.get(f"{API}/expenditures/{e['id']}", headers=de).json()["status"] == "REVERSED"
    p = position(w, "2/18")
    assert (p.actual, p.commitment, p.available) == (D("0"), D("4000"), D("6000"))
    c = client.get(f"{API}/commitments/{po['id']}", headers=de).json()
    assert (c["commitment_status"], c["outstanding"]) == ("APPROVED", "4000.000")
    # الآن يمكن عكس الارتباط نفسه
    rev2 = post_json(client, de, "/reversals", {"source_type": "commitment", "source_id": po["id"],
                                                "adjustment_date": "2026-06-02", "reason": "إلغاء الأمر كاملًا"})
    drive(client, team, "adjustment", rev2["id"])
    assert position(w, "2/18").available == D("10000")
    # القيد العكسي لا يُعكس
    r = client.post(f"{API}/reversals", headers=de, json={"source_type": "adjustment", "source_id": rev["id"],
                                                          "adjustment_date": "2026-06-03", "reason": "عكس العكس"})
    assert r.json()["code"] == "CANNOT_REVERSE_REVERSAL"


def test_manual_adjustment_and_cancel_limits(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    de = team["DATA_ENTRY"]
    adj = post_json(client, de, "/adjustments", {
        "fiscal_year_id": str(w.fy_id), "kind": "ADJUSTMENT", "adjustment_date": "2026-06-01",
        "reason": "تسجيل مصروف فعلي سقط سهوًا بمذكرة التسوية 4",
        "lines": [{"entity_id": str(w.entity_id), "item_id": ids()["2/18"], "component": "ACTUAL", "direction": 1,
                   "amount": "250"}]})
    assert drive(client, team, "adjustment", adj["id"]).json()["doc_status"] == "POSTED"
    assert position(w, "2/18").actual == D("250")
    r = client.post(f"{API}/adjustments", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "kind": "ADJUSTMENT", "adjustment_date": "2026-06-01", "reason": "سبب كاف",
        "lines": [{"entity_id": str(w.entity_id), "item_id": ids()["2/18"], "component": "TRANSFER_IN",
                   "direction": 1, "amount": "1"}]})
    assert r.status_code == 422   # المناقلات لا تُسوّى منفردة
    po = post_json(client, de, "/commitments", {
        "fiscal_year_id": str(w.fy_id), "commitment_type": "OBLIGATION", "commitment_date": "2026-03-10",
        "entity_id": str(w.entity_id), "item_id": ids()["2/18"], "amount": "100", "description": "التزام"})
    drive(client, team, "commitment", po["id"])
    r = client.post(f"{API}/adjustments", headers=de, json={
        "fiscal_year_id": str(w.fy_id), "kind": "COMMITMENT_CANCELLATION", "adjustment_date": "2026-06-01",
        "reason": "إلغاء أكبر من القائم", "commitment_id": po["id"], "cancel_amount": "101"})
    assert r.json()["code"] == "INVALID_AMOUNT"
