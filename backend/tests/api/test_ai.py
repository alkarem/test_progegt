"""المساعد الذكي (المرحلة 14): أدوات للقراءة فقط، التحقق من الأرقام، الإخفاء (D-14)، والحدود.

لا اتصال بالشبكة: عميل Anthropic يُستبدل بعميل محاكٍ يعيد ردودًا مبرمجة ويسجل ما أُرسل إليه.
"""
import copy
import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import new_session
from app.modules.ai import service
from app.modules.ai.models import AIInteraction
from app.modules.alerts import service as alerts
from app.modules.fiscal.models import FiscalYear
from tests.conftest import drive
from tests.ledger_helpers import budget_doc, build_world, post, spec

API = "/api/v1"


def tool_use(name, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=inp)


def say(t):
    return SimpleNamespace(type="text", text=t)


def resp(*blocks, stop=None):
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return SimpleNamespace(stop_reason=stop, content=list(blocks), usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        # نسخة من الرسائل كما أُرسلت (القائمة تتغير لاحقًا داخل الحلقة)
        self.calls.append({**kw, "messages": copy.copy(kw["messages"])})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def tool_results(self):
        out = []
        for c in self.calls:
            for m in c["messages"]:
                if m["role"] == "user" and isinstance(m["content"], list):
                    out += [b for b in m["content"] if b.get("type") == "tool_result"]
        return out


@pytest.fixture()
def ai_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_enabled", True)
    monkeypatch.setattr(s, "ai_mask_personal", True)
    monkeypatch.setattr(s, "ai_questions_per_hour", 30)

    def install(script):
        fake = FakeClient(script)
        monkeypatch.setattr(service, "client_factory", lambda: fake)
        return fake
    return install


def ask(client, h, fy_id, q="كم المتبقي من بند الصيانة؟"):
    return client.post(f"{API}/ai/ask", headers=h, json={"question": q, "fiscal_year_id": str(fy_id)})


# ---------------------------------------------------------------------------
def test_grounding_number_extraction():
    ok = service.ungrounded_numbers("المتاح للبند 2/18 في 2026 هو ١٬٠٠٠٫٠٠٠ دينار (نسبة 85.00%)",
                                    [{"available": "1000.000", "rate": "85", "item_code": "2/18"}], "", ["2026"])
    assert ok == []
    bad = service.ungrounded_numbers("المتاح 1,250.500 والفعلي 3 بنود", [{"available": "1000.000"}], "", [])
    assert bad == ["1250.5"]        # 3 رقم ترقيم عادي؛ 1,250.500 غير موجود في النتائج


def test_disabled_by_default_returns_503(client, team):
    w = build_world()
    r = ask(client, team["BUDGET_CONTROLLER"], w.fy_id)
    assert r.status_code == 503 and r.json()["code"] == "AI_DISABLED"
    assert client.get(f"{API}/ai/status", headers=team["BUDGET_CONTROLLER"]).json()["enabled"] is False


def test_grounded_answer_uses_tool_results_and_is_logged(client, team, ai_on):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    fake = ai_on([
        resp(say("سأتحقق من موقف البند."), tool_use("get_item_position", item_code="2/18", fiscal_year=None, as_of=None)),
        resp(say("الرصيد المتاح للبند 2/18 في السنة المالية 2026 هو 1,000.000 دينار، ولم يُصرف منه شيء.")),
    ])
    r = ask(client, team["BUDGET_CONTROLLER"], w.fy_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OK" and "1,000.000" in body["answer"]
    assert body["sources"][0]["tool"] == "get_item_position" and body["sources"][0]["link"] == "/budget?item=2/18"
    # الطلب نفسه: النموذج والأدوات الصارمة والتحويل الاحتياطي عند الرفض
    first = fake.calls[0]
    assert first["model"] == get_settings().ai_model and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    result = json.loads(fake.tool_results()[0]["content"])
    assert result["position"]["available"] == "1000.000"
    with new_session() as s:
        row = s.scalar(select(AIInteraction))
        assert row.status == "OK" and row.tool_calls[0]["tool"] == "get_item_position"
        assert row.input_tokens == 200 and row.output_tokens == 40
        with pytest.raises(Exception, match="غير قابلة للتعديل"):
            s.execute(text("UPDATE ai_interactions SET answer = 'x'"))
            s.flush()


def test_invented_number_is_retried_then_rejected(client, team, ai_on):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    fake = ai_on([
        resp(tool_use("get_item_position", item_code="2/18", fiscal_year=None, as_of=None)),
        resp(say("المتاح 1,200.000 دينار.")),
        resp(say("بعد المراجعة المتاح 950.000 دينار.")),
    ])
    body = ask(client, team["BUDGET_CONTROLLER"], w.fy_id).json()
    assert body["status"] == "UNGROUNDED" and body["grounding"]["rejected_numbers"] == ["950"]
    assert body["raw"][0]["position"]["available"] == "1000.000"      # تُعرض نتائج الأدوات خامًا
    retry_msg = fake.calls[2]["messages"][-1]["content"]
    assert "1200" in retry_msg


def test_retry_that_fixes_numbers_is_accepted(client, team, ai_on):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    ai_on([
        resp(tool_use("get_item_position", item_code="2/18", fiscal_year=None, as_of=None)),
        resp(say("المتاح نحو 1,100 دينار.")),
        resp(say("المتاح للبند 2/18 هو 1,000.000 دينار.")),
    ])
    body = ask(client, team["BUDGET_CONTROLLER"], w.fy_id).json()
    assert body["status"] == "OK" and body["grounding"]["retried"] is True and "1,000.000" in body["answer"]


def test_personal_data_is_masked_from_tool_results(client, team, ai_on):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    r = client.post(f"{API}/expenditures", headers=team["DATA_ENTRY"], json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": "2026-03-01", "entity_id": str(w.entity_id),
        "item_id": _item(w, "2/18"), "amount": "300",
        "payment_method": "CASH", "description": "صيانة سيارة السيد فلان"})
    assert r.status_code == 201, r.text
    client.post(f"{API}/documents/expenditure/{r.json()['id']}/submit", headers=team["DATA_ENTRY"], json={})
    fake = ai_on([
        resp(tool_use("list_pending_documents", doc_type=None)),
        resp(say("يوجد مستند واحد ينتظر المراجعة بمبلغ 300.000 دينار.")),
    ])
    body = ask(client, team["BUDGET_CONTROLLER"], w.fy_id, "ما العمليات التي تنتظر الاعتماد؟").json()
    assert body["status"] == "OK"
    sent = fake.tool_results()[0]["content"]
    assert "300.000" in sent and "فلان" not in sent and "data_entry" not in sent


def test_tool_error_is_reported_to_model_not_raised(client, team, ai_on):
    w = build_world(items=("2/18",))
    fake = ai_on([
        resp(tool_use("get_item_position", item_code="9/99", fiscal_year=None, as_of=None)),
        resp(say("البند المطلوب غير موجود في السنة المختارة.")),
    ])
    body = ask(client, team["BUDGET_CONTROLLER"], w.fy_id).json()
    assert body["status"] == "OK"
    assert fake.tool_results()[0]["is_error"] is True


def test_refusal_and_provider_error(client, team, ai_on):
    w = build_world(items=("2/18",))
    ai_on([resp(say(""), stop="refusal")])
    assert ask(client, team["BUDGET_CONTROLLER"], w.fy_id).json()["status"] == "REFUSED"
    ai_on([ConnectionError("down")])
    r = ask(client, team["BUDGET_CONTROLLER"], w.fy_id)
    assert r.status_code == 502 and r.json()["code"] == "AI_UNAVAILABLE"
    with new_session() as s:
        assert sorted(s.scalars(select(AIInteraction.status))) == ["ERROR", "REFUSED"]


def test_hourly_question_limit(client, team, ai_on, monkeypatch):
    w = build_world(items=("2/18",))
    monkeypatch.setattr(get_settings(), "ai_questions_per_hour", 1)
    ai_on([resp(say("لا أملك بيانات كافية.")), resp(say("x"))])
    assert ask(client, team["BUDGET_CONTROLLER"], w.fy_id).status_code == 200
    r = ask(client, team["BUDGET_CONTROLLER"], w.fy_id)
    assert r.status_code == 429


def test_summary_draft_is_grounded(client, team, ai_on):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    ai_on([resp(say("بلغ إجمالي الاعتماد للسنة 2026 مبلغ 1,000.000 دينار."))])
    r = client.post(f"{API}/ai/summary", headers=team["BUDGET_CONTROLLER"], json={"fiscal_year_id": str(w.fy_id)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "OK" and r.json()["draft"]


# ---------------------------------------------------------------------------
# الأنماط غير المعتادة الحتمية (12-ai §3)
# ---------------------------------------------------------------------------
def _item(w, code):
    from app.modules.catalog.models import BudgetItem
    with new_session() as s:
        return str(s.scalar(select(BudgetItem.id).where(BudgetItem.code == code)))


def _exp(client, team, w, amount, day, supplier=None, code="2/18"):
    r = client.post(f"{API}/expenditures", headers=team["DATA_ENTRY"], json={
        "fiscal_year_id": str(w.fy_id), "expenditure_date": f"2026-04-{day:02d}", "entity_id": str(w.entity_id),
        "item_id": _item(w, code), "amount": amount, "payment_method": "CASH", "description": "مشتريات",
        "supplier_id": supplier})
    assert r.status_code == 201, r.text
    drive(client, team, "expenditure", r.json()["id"])


def _open_rules(client, h):
    return [a["rule_code"] for a in client.get(f"{API}/alerts", headers=h).json()["items"]]


def test_split_purchase_round_amounts_and_missing_commitment(client, team):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    sup = client.post(f"{API}/suppliers", headers=team["DATA_ENTRY"], json={"name": "شركة التوريدات المتحدة"}).json()["id"]
    _exp(client, team, w, "3000", 1, sup)
    assert "SPLIT_PURCHASE" not in _open_rules(client, team["BUDGET_CONTROLLER"])
    _exp(client, team, w, "2500", 4, sup)            # مجموعهما 5,500 > حد الموافقة 5,000
    assert "SPLIT_PURCHASE" in _open_rules(client, team["BUDGET_CONTROLLER"])

    for d in (10, 12, 14):
        _exp(client, team, w, "6000", d, sup)
    assert "ROUND_AMOUNTS" in _open_rules(client, team["BUDGET_CONTROLLER"])

    _exp(client, team, w, "12000", 20)               # بند صيانة بلا ارتباط فوق 10,000
    assert "EXPENSE_WITHOUT_COMMITMENT" in _open_rules(client, team["BUDGET_CONTROLLER"])


def test_year_end_spike(client, team):
    w = build_world(items=("2/18",))
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "3500"), on=date(2026, 6, 1))
    post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "2000"), on=date(2026, 12, 20))
    with new_session() as s:
        from tests.conftest import begin_write
        begin_write(s)
        fy = s.get(FiscalYear, w.fy_id)
        assert alerts.evaluate_year_end(s, fy, date(2026, 12, 10)) is False     # قبل بدء الفترة
        assert alerts.evaluate_year_end(s, fy, date(2026, 12, 22)) is True      # 2000/6 يوم ≫ 3500/351 يوم
        s.commit()
