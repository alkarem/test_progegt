"""محرك الموافقات عبر الواجهات (المرحلة 5)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from tests.ledger_helpers import actual, budget_doc, build_world

API = "/api/v1"


def item(code):
    with new_session() as s:
        return str(s.scalar(select(BudgetItem.id).where(BudgetItem.code == code)))


@pytest.fixture()
def team(user_factory):
    return {r: user_factory(r.lower(), r) for r in
            ("DATA_ENTRY", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER")}


def new_doc(client, h, w, kind="ORIGINAL_BUDGET", amounts=None):
    amounts = amounts or {"2/18": "100000"}
    body = {"fiscal_year_id": str(w.fy_id), "kind": kind, "doc_date": "2026-01-10", "description": "مستند اختبار",
            "lines": [{"entity_id": str(w.entity_id), "item_id": item(c), "amount": a} for c, a in amounts.items()]}
    r = client.post(f"{API}/budget-documents", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def act(client, h, doc_id, action, **body):
    return client.post(f"{API}/documents/budget_document/{doc_id}/{action}", headers=h, json=body)


def run_full(client, team, doc_id):
    for role, step in (("DATA_ENTRY", None), ("FINANCIAL_REVIEWER", "FINANCIAL_REVIEW"),
                       ("BUDGET_CONTROLLER", "BUDGET_CONTROL"), ("SUPERVISOR", "SUPERVISOR_APPROVAL"),
                       ("APPROVER", "FINAL_APPROVAL")):
        r = act(client, team[role], doc_id, "submit" if step is None else "approve", expected_step=step)
        assert r.status_code == 200, (role, r.text)
    return r.json()


def test_full_approval_path_posts_document(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    r = act(client, team["DATA_ENTRY"], doc_id, "submit").json()
    assert (r["doc_status"], r["current_step"]) == ("SUBMITTED", "FINANCIAL_REVIEW")
    for role, step in (("FINANCIAL_REVIEWER", "BUDGET_CONTROL"), ("BUDGET_CONTROLLER", "SUPERVISOR_APPROVAL"),
                       ("SUPERVISOR", "FINAL_APPROVAL")):
        r = act(client, team[role], doc_id, "approve").json()
        assert (r["doc_status"], r["current_step"]) == ("IN_REVIEW", step)
    r = act(client, team["APPROVER"], doc_id, "approve", comment="معتمد").json()
    assert (r["doc_status"], r["state"], r["current_step"]) == ("POSTED", "COMPLETED", None)
    pos = client.get(f"{API}/budget-position", headers=team["APPROVER"],
                     params={"fiscal_year_id": str(w.fy_id)}).json()
    assert next(x for x in pos if x["item_code"] == "2/18")["position"]["available"] == "100000.000"
    hist = client.get(f"{API}/documents/budget_document/{doc_id}/history", headers=team["DATA_ENTRY"]).json()
    assert [h["action"] for h in hist] == ["SUBMIT", "APPROVE", "APPROVE", "APPROVE", "POST"]
    assert hist[0]["budget_check"]["ok"] is True
    # المرحّل غير قابل للتعديل: عبر الواجهة وعبر قاعدة البيانات
    r = client.patch(f"{API}/budget-documents/{doc_id}", headers=team["DATA_ENTRY"], json={"description": "تلاعب"})
    assert r.status_code == 409 and r.json()["code"] == "NOT_EDITABLE"
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError, match="حالة نهائية"):
            s.execute(text("UPDATE budget_documents SET description = 'x' WHERE id = :i"), {"i": doc_id})
        s.rollback()
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError, match="مقفلة"):
            s.execute(text("UPDATE budget_document_lines SET amount = 1 WHERE document_id = :i"), {"i": doc_id})


def test_steps_cannot_be_skipped_or_done_by_wrong_role(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    for role in ("BUDGET_CONTROLLER", "SUPERVISOR", "APPROVER"):
        r = act(client, team[role], doc_id, "approve")
        assert r.status_code == 403 and r.json()["code"] == "PERMISSION_DENIED"
    assert act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve", expected_step="BUDGET_CONTROL").json()["code"] \
        == "STEP_CHANGED"


def test_segregation_of_duties(client, user_factory, team):
    w = build_world(basis="APPROPRIATION")
    both = user_factory("both", "DATA_ENTRY", "FINANCIAL_REVIEWER")
    doc_id = new_doc(client, both, w)
    act(client, both, doc_id, "submit")
    r = act(client, both, doc_id, "approve")
    assert r.status_code == 403 and r.json()["code"] == "SEGREGATION_OF_DUTIES"
    multi = user_factory("multi", "FINANCIAL_REVIEWER", "BUDGET_CONTROLLER")
    assert act(client, multi, doc_id, "approve").status_code == 200
    r = act(client, multi, doc_id, "approve")
    assert r.status_code == 403 and r.json()["code"] == "SEGREGATION_OF_DUTIES"


def test_return_edit_resubmit_starts_new_round(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve")
    assert act(client, team["BUDGET_CONTROLLER"], doc_id, "return").json()["code"] == "COMMENT_REQUIRED"
    r = act(client, team["BUDGET_CONTROLLER"], doc_id, "return", comment="المبلغ يحتاج قرارًا مرفقًا").json()
    assert r["doc_status"] == "RETURNED"
    r = client.patch(f"{API}/budget-documents/{doc_id}", headers=team["DATA_ENTRY"],
                     json={"description": "بعد التصحيح"})
    assert r.status_code == 200
    r = act(client, team["DATA_ENTRY"], doc_id, "submit").json()
    assert (r["round"], r["current_step"]) == (2, "FINANCIAL_REVIEW")
    # المراجع نفسه يستطيع الاعتماد في الجولة الجديدة
    assert act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve").status_code == 200


def test_reject_is_final(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    r = act(client, team["FINANCIAL_REVIEWER"], doc_id, "reject", comment="مستند مكرر").json()
    assert r["doc_status"] == "REJECTED"
    assert act(client, team["DATA_ENTRY"], doc_id, "submit").status_code == 409
    assert client.patch(f"{API}/budget-documents/{doc_id}", headers=team["DATA_ENTRY"],
                        json={"description": "x y z"}).status_code == 409


def test_budget_control_blocks_shortfall_and_override_path(client, team, user_factory):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    actual(w, "2/18", "900")
    doc_id = new_doc(client, team["DATA_ENTRY"], w, kind="BUDGET_DECREASE", amounts={"2/18": "300"})
    r = act(client, team["DATA_ENTRY"], doc_id, "submit")
    hist = client.get(f"{API}/documents/budget_document/{doc_id}/history", headers=team["DATA_ENTRY"]).json()
    assert hist[0]["budget_check"]["ok"] is False  # التقديم معلوماتي
    act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve")
    r = act(client, team["BUDGET_CONTROLLER"], doc_id, "approve")
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_BUDGET"
    assert r.json()["title"] == "لا يوجد اعتماد متاح كافٍ لهذه العملية."
    assert r.json()["details"]["shortfall"] == "200.000"
    assert act(client, team["BUDGET_CONTROLLER"], doc_id, "approve", acknowledge_shortfall=True).json()["code"] \
        == "COMMENT_REQUIRED"
    assert act(client, team["BUDGET_CONTROLLER"], doc_id, "approve", acknowledge_shortfall=True,
               comment="يُعرض على المعتمد مع طلب استثناء").status_code == 200
    act(client, team["SUPERVISOR"], doc_id, "approve")
    r = act(client, team["APPROVER"], doc_id, "approve")
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_BUDGET"
    # منحة استثناء من معتمد آخر للمعتمد النهائي
    other = user_factory("approver_b", "APPROVER")
    me = client.get(f"{API}/auth/me", headers=team["APPROVER"]).json()["id"]
    now = datetime.now(UTC)
    g = client.post(f"{API}/override-grants", headers=other, json={
        "user_id": me, "budget_line_id": str(w.lines["2/18"]), "max_amount": "250",
        "valid_from": (now - timedelta(minutes=1)).isoformat(), "valid_to": (now + timedelta(hours=1)).isoformat(),
        "reason": "قرار تخفيض بموافقة الوزارة"}).json()
    r = act(client, team["APPROVER"], doc_id, "approve", override_grant_id=g["id"])
    assert r.status_code == 200 and r.json()["doc_status"] == "POSTED"
    hist = client.get(f"{API}/documents/budget_document/{doc_id}/history", headers=team["DATA_ENTRY"]).json()
    assert hist[-1]["budget_check"]["override_used"] == "200.000"


def test_inbox_shows_only_actionable_documents(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    rev = client.get(f"{API}/inbox", headers=team["FINANCIAL_REVIEWER"]).json()
    assert [(r["source_id"], r["step"]) for r in rev] == [(doc_id, "FINANCIAL_REVIEW")]
    assert client.get(f"{API}/inbox", headers=team["BUDGET_CONTROLLER"]).json() == []
    assert client.get(f"{API}/inbox", headers=team["DATA_ENTRY"]).json() == []


def test_delegation_allows_acting_on_behalf(client, team, user_factory):
    w = build_world(basis="APPROPRIATION")
    deputy = user_factory("deputy", "FINANCIAL_REVIEWER")
    deputy_id = client.get(f"{API}/auth/me", headers=deputy).json()["id"]
    sup_id = client.get(f"{API}/auth/me", headers=team["SUPERVISOR"]).json()["id"]
    now = datetime.now(UTC)
    r = client.post(f"{API}/delegations", headers=team["SUPERVISOR"], json={
        "delegate_id": deputy_id, "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "valid_to": (now + timedelta(days=3)).isoformat(), "reason": "إجازة سنوية"})
    assert r.status_code == 201
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve")
    act(client, team["BUDGET_CONTROLLER"], doc_id, "approve")
    inbox = client.get(f"{API}/inbox", headers=deputy).json()
    assert inbox[0]["on_behalf_of"] == sup_id
    assert act(client, deputy, doc_id, "approve").status_code == 200
    hist = client.get(f"{API}/documents/budget_document/{doc_id}/history", headers=deputy).json()
    assert hist[-1]["on_behalf_of"] == sup_id and hist[-1]["actor_id"] == deputy_id


def test_amount_threshold_skips_supervisor_step(client, team, user_factory):
    w = build_world(basis="APPROPRIATION")
    admin = user_factory("admin", "SYSTEM_ADMIN")
    defs = client.get(f"{API}/workflow-definitions", headers=team["APPROVER"]).json()
    bd = next(d for d in defs if d["code"] == "budget_document")
    sup = next(s for s in bd["steps"] if s["code"] == "SUPERVISOR_APPROVAL")
    final = next(s for s in bd["steps"] if s["posts"])
    assert client.patch(f"{API}/workflow-steps/{final['id']}", headers=admin, json={"min_amount": "5"}
                        ).json()["code"] == "POSTING_STEP_FIXED"
    assert client.patch(f"{API}/workflow-steps/{sup['id']}", headers=admin,
                        json={"min_amount": "1000000"}).status_code == 200
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    act(client, team["FINANCIAL_REVIEWER"], doc_id, "approve")
    r = act(client, team["BUDGET_CONTROLLER"], doc_id, "approve").json()
    assert r["current_step"] == "FINAL_APPROVAL"


def test_cancel_rules(client, team):
    w = build_world(basis="APPROPRIATION")
    d1 = new_doc(client, team["DATA_ENTRY"], w)
    assert act(client, team["DATA_ENTRY"], d1, "cancel", comment="أُنشئ بالخطأ").json()["doc_status"] == "CANCELLED"
    d2 = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], d2, "submit")
    assert act(client, team["DATA_ENTRY"], d2, "cancel", comment="تراجع").status_code == 403
    assert act(client, team["BUDGET_CONTROLLER"], d2, "cancel", comment="لم يعد مطلوبًا").status_code == 200
    d3 = new_doc(client, team["DATA_ENTRY"], w)
    run_full(client, team, d3)
    r = act(client, team["BUDGET_CONTROLLER"], d3, "cancel", comment="بعد الترحيل")
    assert r.status_code == 409


def test_workflow_actions_are_immutable(client, team):
    w = build_world(basis="APPROPRIATION")
    doc_id = new_doc(client, team["DATA_ENTRY"], w)
    act(client, team["DATA_ENTRY"], doc_id, "submit")
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        with pytest.raises(DBAPIError, match="غير قابلة للتعديل"):
            s.execute(text("UPDATE workflow_actions SET comment = 'x'"))
