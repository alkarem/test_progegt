"""المساعد المالي الذكي (12-ai §2 و§5).

المبدأ: النموذج يختار أدوات قراءة معرّفة مسبقًا ويصوغ الإجابة؛ لا يحسب ولا يكتب.
التحقق من الأرقام (Grounding): كل رقم في الإجابة يجب أن يظهر في نتائج الأدوات (أو في السؤال نفسه).
إن فشل التحقق يُعاد التوليد مرة واحدة مع ذكر الأرقام المرفوضة، وإن فشل ثانية تُعرض نتائج الأدوات جدولًا خامًا.
"""
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import DomainError, NotFound
from app.core.ratelimit import RateLimited
from app.modules.ai import tools
from app.modules.ai.models import AIInteraction
from app.modules.fiscal.models import FiscalYear


class AIDisabled(DomainError):
    status_code = 503
    code = "AI_DISABLED"


class AIUnavailable(DomainError):
    status_code = 502
    code = "AI_UNAVAILABLE"


SYSTEM = """أنت المساعد المالي في «نظام مراقبة الاعتمادات والمصروفات الحكومية» (الباب الثاني).
تجيب عن أسئلة المستخدمين بالعربية الفصحى المختصرة، اعتمادًا على الأدوات فقط.

قواعد صارمة:
- كل رقم تذكره يجب أن يُنسخ كما هو من نتيجة أداة. لا تجمع ولا تطرح ولا تحسب نسبًا بنفسك؛ إن احتجت رقمًا مشتقًا فاستدعِ الأداة التي تعيده. لا تقرّب الأرقام.
- اكتب المبالغ بثلاث خانات عشرية كما وردت (الدينار = 1000 درهم).
- اذكر السنة المالية ورمز البند في الإجابة. إن لم تكفِ الأدوات للإجابة فقل ذلك صراحة واقترح التقرير المناسب.
- أنت للقراءة فقط: لا تعتمد ولا ترحّل ولا تعدّل شيئًا، ولا تعد بفعل ذلك.
- الأسئلة التي تعني «المتبقي» أو «الرصيد» تقصد الرصيد المتاح (available).
- لا تخترع أسماء أشخاص أو موردين.
- الأرباع: الأول 01-01 إلى 03-31، الثاني 04-01 إلى 06-30، الثالث 07-01 إلى 09-30، الرابع 10-01 إلى 12-31."""

SUMMARY_PROMPT = """اكتب مسودة ملخص إداري قصير (فقرتان إلى ثلاث) بالعربية للسنة المالية {year} اعتمادًا على البيانات التالية فقط.
اذكر نسبة التنفيذ، وأكثر البنود استخدامًا، والبنود المتجاوزة أو القريبة من الاستنفاد، والارتباطات القائمة.
انسخ كل رقم كما هو من البيانات دون أي حساب أو تقريب.

البيانات:
{data}"""

_NUM = re.compile(r"(?<![\w.])\d[\d,٬]*(?:[.٫]\d+)?")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# ---------------------------------------------------------------------------
# التحقق من الأرقام
# ---------------------------------------------------------------------------
def _numbers(text: str) -> set[Decimal]:
    out = set()
    for m in _NUM.findall(text.translate(_AR_DIGITS)):
        try:
            out.add(Decimal(m.replace(",", "").replace("٬", "").replace("٫", ".")).normalize())
        except InvalidOperation:
            continue
    return out


def _allowed(results: list, question: str, extra: list[str]) -> set[Decimal]:
    allowed: set[Decimal] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            allowed.add(Decimal(len(v)))   # عدد العناصر مسموح ذكره
            for x in v:
                walk(x)
        elif isinstance(v, bool) or v is None:
            return
        else:
            allowed.update(_numbers(str(v)))

    walk(results)
    for t in [question, *extra]:
        allowed.update(_numbers(t))
    return allowed


def ungrounded_numbers(answer: str, results: list, question: str, extra: list[str]) -> list[str]:
    allowed = _allowed(results, question, extra)
    # 1 و2 و3 و4 تُستخدم للترقيم وأرباع السنة؛ لا تحمل معنى ماليًا
    trivial = {Decimal(n) for n in range(0, 5)}
    return sorted({f"{n:f}" for n in _numbers(answer) if n not in allowed and n not in trivial})


# ---------------------------------------------------------------------------
# العميل
# ---------------------------------------------------------------------------
def _client():
    import anthropic
    key = get_settings().ai_api_key
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


client_factory = _client   # تستبدله الاختبارات بعميل محاكٍ


def _create(client, **kw):
    s = get_settings()
    return client.beta.messages.create(
        model=s.ai_model, max_tokens=16000, thinking={"type": "adaptive"},
        output_config={"effort": s.ai_effort}, betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)


def _echo(content: list) -> list:
    """بعد تحويل الطلب لنموذج بديل في منتصف الإخراج: تُحذف كتل التفكير واستدعاءات الأدوات السابقة لآخر كتلة fallback."""
    idx = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if idx is None:
        return content
    return [b for i, b in enumerate(content)
            if i > idx or b.type not in ("thinking", "redacted_thinking", "tool_use", "fallback")]


def _text(resp) -> str:
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
def _guard(session: Session, p: Principal) -> None:
    s = get_settings()
    if not s.ai_enabled:
        raise AIDisabled("المساعد الذكي معطل في هذا النظام.")
    since = datetime.now(UTC) - timedelta(hours=1)
    n = session.scalar(select(func.count()).select_from(AIInteraction).where(
        AIInteraction.user_id == p.user_id, AIInteraction.created_at >= since))
    if n >= s.ai_questions_per_hour:
        raise RateLimited(f"بلغت الحد الأقصى ({s.ai_questions_per_hour}) من الأسئلة في الساعة.",
                          details={"retry_after": 3600})


def _year(session: Session, p: Principal, fiscal_year_id: uuid.UUID) -> FiscalYear:
    fy = session.get(FiscalYear, fiscal_year_id)
    if fy is None:
        raise NotFound("السنة المالية غير موجودة.")
    p.require_scope("FISCAL_YEAR", fy.id)
    return fy


def ask(session: Session, p: Principal, question: str, fiscal_year_id: uuid.UUID) -> dict:
    _guard(session, p)
    s = get_settings()
    fy = _year(session, p, fiscal_year_id)
    ctx = tools.ToolContext(session, p, fy)
    client = client_factory()
    started = time.monotonic()
    context = f"السنة المالية المختارة: {fy.year}. تاريخ اليوم: {datetime.now(UTC).date().isoformat()}."
    messages: list = [{"role": "user", "content": f"{context}\n\nالسؤال: {question}"}]
    calls: list[dict] = []
    usage = {"in": 0, "out": 0}
    status, answer, grounding, retried = "OK", "", None, False

    def model_turn():
        try:
            r = _create(client, system=SYSTEM, tools=tools.definitions_for_model(), messages=messages)
        except Exception as exc:   # أخطاء المزود لا تمس النظام المالي؛ تُسجَّل وتُعاد رسالة واضحة
            raise AIUnavailable("تعذر الاتصال بخدمة الذكاء الاصطناعي.", details={"error": type(exc).__name__}) from exc
        usage["in"] += getattr(r.usage, "input_tokens", 0) or 0
        usage["out"] += getattr(r.usage, "output_tokens", 0) or 0
        return r

    try:
        for _ in range(s.ai_max_tool_rounds + 2):
            resp = model_turn()
            if resp.stop_reason == "refusal":
                status, answer = "REFUSED", "لم يتمكن المساعد من الإجابة عن هذا السؤال."
                break
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "tool_use" and uses:
                if len(calls) >= s.ai_max_tool_rounds * 4:
                    status, answer = "ERROR", "تجاوز المساعد الحد المسموح من استدعاءات الأدوات."
                    break
                messages.append({"role": "assistant", "content": _echo(resp.content)})
                results = []
                for u in uses:
                    try:
                        out = tools.call(ctx, u.name, dict(u.input))
                        calls.append({"tool": u.name, "input": u.input, "result": out})
                        results.append({"type": "tool_result", "tool_use_id": u.id,
                                        "content": json.dumps(out, ensure_ascii=False, default=str)})
                    except DomainError as err:
                        calls.append({"tool": u.name, "input": u.input, "error": err.message})
                        results.append({"type": "tool_result", "tool_use_id": u.id, "content": err.message,
                                        "is_error": True})
                messages.append({"role": "user", "content": results})
                continue
            answer = _text(resp)
            bad = ungrounded_numbers(answer, [c.get("result") for c in calls], question, [str(fy.year), context])
            if not bad:
                break
            if retried:
                status, grounding = "UNGROUNDED", {"rejected_numbers": bad}
                answer = ("تعذر التحقق من كل الأرقام في الصياغة، لذا تُعرض نتائج الأدوات كما هي دون صياغة."
                          if calls else "لم يستند المساعد إلى بيانات النظام في إجابته؛ أعد صياغة السؤال.")
                break
            retried = True
            messages.append({"role": "assistant", "content": _echo(resp.content)})
            messages.append({"role": "user", "content":
                             f"الأرقام التالية في إجابتك لا تظهر في نتائج الأدوات: {', '.join(bad)}. "
                             "أعد كتابة الإجابة مستخدمًا أرقام الأدوات حرفيًا فقط، واستدعِ أداة إن احتجت رقمًا آخر."})
        else:
            status, answer = "ERROR", "لم يكمل المساعد الإجابة ضمن الحد المسموح من الخطوات."
    except AIUnavailable:
        _log(session, p, fy, "ASK", question, calls, None, "ERROR", None, usage, started)
        raise

    grounding = grounding or {"checked": True, "retried": retried}
    row = _log(session, p, fy, "ASK", question, calls, answer, status, grounding, usage, started)
    return {"id": row.id, "status": status, "answer": answer, "grounding": grounding,
            "sources": [_source_card(c) for c in calls if "result" in c],
            "raw": [c["result"] for c in calls if "result" in c] if status == "UNGROUNDED" else None}


def summary(session: Session, p: Principal, fiscal_year_id: uuid.UUID) -> dict:
    """مسودة ملخص إداري (12-ai §5): الأرقام من أدوات حتمية، والنموذج يصوغ فقط."""
    _guard(session, p)
    fy = _year(session, p, fiscal_year_id)
    ctx = tools.ToolContext(session, p, fy)
    started = time.monotonic()
    rows12, _ = tools._run(ctx, "RPT-12", {"fiscal_year_id": str(fy.id)})
    data = {"fiscal_year_summary": tools._mask(rows12),
            "most_used_items": tools.list_items_by_utilization(ctx, 50, 1000)["items"][:6],
            "overbudget": tools.list_overbudget_items(ctx)["items"]}
    client = client_factory()
    usage = {"in": 0, "out": 0}
    prompt = SUMMARY_PROMPT.format(year=fy.year, data=json.dumps(data, ensure_ascii=False, default=str))
    messages: list = [{"role": "user", "content": prompt}]
    status, draft, grounding = "OK", "", {"checked": True, "retried": False}
    for attempt in range(2):
        try:
            resp = _create(client, system=SYSTEM, messages=messages)
        except Exception as exc:
            _log(session, p, fy, "SUMMARY", "ملخص إداري", [], None, "ERROR", None, usage, started)
            raise AIUnavailable("تعذر الاتصال بخدمة الذكاء الاصطناعي.", details={"error": type(exc).__name__}) from exc
        usage["in"] += getattr(resp.usage, "input_tokens", 0) or 0
        usage["out"] += getattr(resp.usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            status, draft = "REFUSED", ""
            break
        draft = _text(resp)
        bad = ungrounded_numbers(draft, [data], "", [str(fy.year)])
        if not bad:
            grounding["retried"] = attempt == 1
            break
        if attempt == 1:
            status, draft, grounding = "UNGROUNDED", "", {"rejected_numbers": bad}
            break
        messages += [{"role": "assistant", "content": _echo(resp.content)},
                     {"role": "user", "content": f"الأرقام {', '.join(bad)} غير موجودة في البيانات؛ أعد الكتابة بأرقام البيانات حرفيًا."}]
    row = _log(session, p, fy, "SUMMARY", "ملخص إداري", [{"tool": "summary_data", "result": data}], draft, status,
               grounding, usage, started)
    return {"id": row.id, "status": status, "draft": draft, "grounding": grounding, "data": data}


def _source_card(call: dict) -> dict:
    r = call["result"]
    return {"tool": call["tool"], "source": r.get("source"), "fiscal_year": r.get("fiscal_year"),
            "as_of": r.get("as_of"), "link": r.get("link")}


def _log(session, p, fy, kind, question, calls, answer, status, grounding, usage, started) -> AIInteraction:
    row = AIInteraction(user_id=p.user_id, fiscal_year_id=fy.id if fy else None, kind=kind, question=question,
                        tool_calls=json.loads(json.dumps(calls, ensure_ascii=False, default=str)), answer=answer,
                        status=status, grounding=grounding, model=get_settings().ai_model,
                        input_tokens=usage["in"], output_tokens=usage["out"],
                        duration_ms=int((time.monotonic() - started) * 1000))
    session.add(row)
    session.flush()
    return row
