"""التنبيهات والإشعارات ولوحة القيادة والبحث الشامل (المرحلة 10)."""
from datetime import date

from sqlalchemy import select

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.conftest import drive
from tests.ledger_helpers import actual, budget_doc, build_world, post, spec

API = "/api/v1"
PDF = b"%PDF-1.4\n%%EOF"


def ids():
    with new_session() as s:
        return {c: str(i) for c, i in s.execute(select(BudgetItem.code, BudgetItem.id))}


def new_exp(client, h, w, code, amount, **kw):
    r = client.post(f"{API}/expenditures", headers=h, json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-05-10", "entity_id": str(w.entity_id),
        "item_id": ids()[code], "amount": amount, "payment_method": "CASH", "description": "صرف"} | kw)
    assert r.status_code == 201, r.text
    return r.json()


def alerts(client, h, **params):
    return client.get(f"{API}/alerts", headers=h, params=params).json()["items"]


def test_low_balance_alert_escalates_and_auto_resolves(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    e = new_exp(client, team["DATA_ENTRY"], w, "2/18", "850")
    drive(client, team, "expenditure", e["id"])
    a = [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "LOW_BALANCE"]
    assert len(a) == 1 and a[0]["severity"] == "WARNING" and "85.00%" in a[0]["message"]
    e2 = new_exp(client, team["DATA_ENTRY"], w, "2/18", "70")
    drive(client, team, "expenditure", e2["id"])
    a = [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "LOW_BALANCE"]
    assert len(a) == 1 and a[0]["severity"] == "HIGH"          # لا تكرار، والشدة تتصاعد
    budget_doc(w, "BUDGET_INCREASE", {"2/18": "5000"})
    client.post(f"{API}/alerts/evaluate", headers=team["BUDGET_CONTROLLER"])
    assert not [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "LOW_BALANCE"]


def test_budget_exceeded_alert_from_periodic_run(client, team):
    w = build_world(year=2023, basis="AUTHORIZATION", items=("2/25",))
    post(w, spec(w, "2/25", "ACTUAL_EXPENDITURE", "ACTUAL", "13000"), historical_exception=True, on=date(2023, 12, 31))
    r = client.post(f"{API}/alerts/evaluate", headers=team["BUDGET_CONTROLLER"]).json()
    a = [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "BUDGET_EXCEEDED"]
    assert a[0]["severity"] == "CRITICAL" and "13,000.000" in a[0]["message"]
    assert "BALANCE_MISMATCH" not in r["raised"]


def test_missing_attachment_alert_resolves_when_attached(client, team, settings_tmp_storage):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    e = new_exp(client, team["DATA_ENTRY"], w, "2/18", "10")
    client.post(f"{API}/documents/expenditure/{e['id']}/submit", headers=team["DATA_ENTRY"], json={})
    assert [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "MISSING_ATTACHMENT"]
    client.post(f"{API}/attachments", headers=team["DATA_ENTRY"], files={"file": ("inv.pdf", PDF)},
                data={"source_type": "expenditure", "source_id": e["id"]})
    assert not [x for x in alerts(client, team["BUDGET_CONTROLLER"]) if x["rule_code"] == "MISSING_ATTACHMENT"]


def test_duplicate_and_unusual_transaction_alerts(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000000"})
    for amt in ("100", "110", "95", "105", "98", "102"):
        actual(w, "2/18", amt)
    sup = client.post(f"{API}/suppliers", headers=team["DATA_ENTRY"], json={"name": "شركة منارة جالو"}).json()["id"]
    e1 = new_exp(client, team["DATA_ENTRY"], w, "2/18", "6250", supplier_id=sup, document_no="M-1")
    drive(client, team, "expenditure", e1["id"])
    e2 = new_exp(client, team["DATA_ENTRY"], w, "2/18", "6250", supplier_id=sup, document_no="M-2")
    drive(client, team, "expenditure", e2["id"])
    codes = [x["rule_code"] for x in alerts(client, team["BUDGET_CONTROLLER"])]
    assert "DUPLICATE_DOCUMENT" in codes and "UNUSUAL_TRANSACTION" in codes


def test_notifications_follow_the_workflow(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    e = new_exp(client, team["DATA_ENTRY"], w, "2/18", "10")
    client.post(f"{API}/documents/expenditure/{e['id']}/submit", headers=team["DATA_ENTRY"], json={})
    n = client.get(f"{API}/notifications", headers=team["FINANCIAL_REVIEWER"]).json()["items"]
    assert n and n[0]["title"].startswith("بانتظار إجرائك") and n[0]["link"].endswith(e["id"])
    assert client.get(f"{API}/notifications/unread-count", headers=team["FINANCIAL_REVIEWER"]).json()["count"] == 1
    client.post(f"{API}/documents/expenditure/{e['id']}/return", headers=team["FINANCIAL_REVIEWER"],
                json={"comment": "أرفق الفاتورة"})
    mine = client.get(f"{API}/notifications", headers=team["DATA_ENTRY"]).json()["items"]
    assert mine[0]["title"].startswith("أُرجع للتعديل") and mine[0]["body"] == "أرفق الفاتورة"
    client.post(f"{API}/notifications/read", headers=team["DATA_ENTRY"], json={})
    assert client.get(f"{API}/notifications/unread-count", headers=team["DATA_ENTRY"]).json()["count"] == 0


def test_dashboard_kpis_and_charts(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/6", "2/16", "2/18"))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "180000", "2/16": "40000", "2/6": "20000"})
    from tests.ledger_helpers import transfer
    transfer(w, "2/6", "2/16", "20000")
    actual(w, "2/18", "150000")
    post(w, spec(w, "2/16", "COMMITMENT", "COMMITMENT", "50000"))
    post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "1000"), on=date(2026, 12, 31), date_is_estimated=True)
    h = team["APPROVER"]
    k = client.get(f"{API}/dashboard/kpis", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert (k["total_budget"], k["total_actual"], k["total_commitments"], k["total_available"]) == \
        ("240000.000", "151000.000", "50000.000", "39000.000")
    assert k["execution_rate"] == "62.92" and k["transfers_count"] == 0  # المناقلة هنا قيود مباشرة بلا مستند
    top = client.get(f"{API}/dashboard/charts/top_spending", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert top[0]["item_code"] == "2/18"
    low = client.get(f"{API}/dashboard/charts/low_balance", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert {r["item_code"] for r in low} == {"2/18", "2/16"}
    m = client.get(f"{API}/dashboard/charts/monthly_expenditure", headers=h,
                   params={"fiscal_year_id": str(w.fy_id)}).json()
    assert m["months"][2]["actual"] == "150000.000" and m["estimated_dates_total"] == "1000.000"
    yoy = client.get(f"{API}/dashboard/charts/year_over_year", headers=h, params={"fiscal_year_id": str(w.fy_id)}).json()
    assert yoy == [{"year": 2026, "budget": "240000.000", "actual": "151000.000"}]


def test_global_search(client, team):
    w = build_world(basis="APPROPRIATION", items=("2/18",))
    de = team["DATA_ENTRY"]
    sup = client.post(f"{API}/suppliers", headers=de, json={"name": "محلات الإتقان للقرطاسية"}).json()["id"]
    new_exp(client, de, w, "2/18", "19700", supplier_id=sup, document_no="EXP-77", payment_order_no="أ-991")
    h = team["FINANCIAL_REVIEWER"]

    def docs(q, **params):
        return client.get(f"{API}/search", headers=h, params={"q": q} | params).json()

    assert [d["doc_no"] for d in docs("EXP-77")["documents"]] == ["EXP-77"]
    assert [d["doc_no"] for d in docs("الاتقان")["documents"]] == ["EXP-77"]       # همزة مختلفة
    assert [d["doc_no"] for d in docs("19,700")["documents"]] == ["EXP-77"]        # بالقيمة
    assert [d["doc_no"] for d in docs("2/18")["documents"]] == ["EXP-77"]          # بالبند
    assert [d["doc_no"] for d in docs("أ-991")["documents"]] == ["EXP-77"]         # أمر الصرف
    assert docs("الاتقان")["suppliers"][0]["name"] == "محلات الإتقان للقرطاسية"
    assert docs("' OR 1=1 --")["documents"] == []                                     # حقن لا أثر له
    from tests.conftest import create_user, login
    create_user("viewer1", ["REPORT_VIEWER"])
    rv = login(client, "viewer1")
    assert client.get(f"{API}/search", headers=rv, params={"q": "EXP-77"}).json()["documents"] == []
