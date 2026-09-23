"""أدوات المساعد الذكي (12-ai §2): قراءة فقط، وكلها تمر عبر محرك التقارير نفسه.

كل أداة تستدعي تعريف تقرير قائمًا (بصلاحياته ونطاقه)، ثم تُرجع صفوفًا مختصرة. لا SQL حر، ولا كتابة.
عند تفعيل ai_mask_personal (D-14) تُحذف أسماء الأشخاص والنصوص الحرة، فيصل للنموذج أرقام ورموز فقط.
"""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import NotFound, ValidationFailed
from app.modules.alerts.models import Alert
from app.modules.catalog.models import BudgetItem
from app.modules.fiscal.models import FiscalYear
from app.modules.ledger.models import BudgetLine
from app.modules.reports import definitions  # noqa: F401  (تسجيل التقارير)
from app.modules.reports.engine import REGISTRY, get_def, jsonable_row

# حقول قد تحمل أسماء أشخاص أو نصًا حرًا يكتبه المستخدمون
PERSONAL = {"created_by", "approved_by", "posted_by", "description", "reason", "beneficiary", "supplier",
            "supplier_name", "notes", "purpose", "user", "user_name"}


@dataclass
class ToolContext:
    session: Session
    principal: Principal
    fiscal_year: FiscalYear   # السنة المختارة في الواجهة (افتراضية الأدوات)


def _mask(rows: list[dict]) -> list[dict]:
    if not get_settings().ai_mask_personal:
        return rows
    return [{k: v for k, v in r.items() if k not in PERSONAL} for r in rows]


def _year(ctx: ToolContext, year: int | None) -> FiscalYear:
    if year is None or year == ctx.fiscal_year.year:
        fy = ctx.fiscal_year
    else:
        fy = ctx.session.scalar(select(FiscalYear).where(FiscalYear.year == year))
        if fy is None:
            raise NotFound(f"لا توجد سنة مالية {year}.")
    ctx.principal.require_scope("FISCAL_YEAR", fy.id)
    return fy


def _run(ctx: ToolContext, code: str, params: dict) -> tuple[list[dict], dict]:
    d = get_def(code)
    ctx.principal.require(d.permission)
    data = d.run(ctx.session, ctx.principal, params)
    return [jsonable_row(r) for r in data.rows], jsonable_row(data.totals)


def _code(item: str) -> str:
    return item.replace(" ", "").strip()


def _line_id(ctx: ToolContext, fy: FiscalYear, item: str) -> uuid.UUID:
    lid = ctx.session.scalar(select(BudgetLine.id).join(BudgetItem, BudgetItem.id == BudgetLine.item_id).where(
        BudgetLine.fiscal_year_id == fy.id, BudgetItem.code == _code(item)).limit(1))
    if lid is None:
        raise NotFound(f"لا يوجد سطر ميزانية للبند {item} في {fy.year}.")
    return lid


def _source(fy: FiscalYear, what: str) -> dict:
    return {"source": what, "fiscal_year": fy.year, "as_of": date.today().isoformat()}


# ---------------------------------------------------------------------------
def get_item_position(ctx: ToolContext, item_code: str, fiscal_year: int | None = None,
                      as_of: str | None = None) -> dict:
    fy = _year(ctx, fiscal_year)
    rows, _ = _run(ctx, "RPT-01", {"fiscal_year_id": str(fy.id), "as_of": as_of})
    row = next((r for r in rows if r["item_code"] == _code(item_code)), None)
    if row is None:
        raise NotFound(f"البند {item_code} غير موجود أو خارج نطاقك.")
    level = next((r["level"] for r in _run(ctx, "RPT-07", {"fiscal_year_id": str(fy.id)})[0]
                  if r["item_code"] == row["item_code"]), None)
    return {**_source(fy, f"موقف البند {row['item_code']}"), "position": row, "level": level,
            "link": f"/budget?item={row['item_code']}"}


def sum_actual(ctx: ToolContext, fiscal_year: int | None = None, date_from: str | None = None,
               date_to: str | None = None, item_codes: list[str] | None = None) -> dict:
    fy = _year(ctx, fiscal_year)
    rows, _ = _run(ctx, "RPT-20", {"fiscal_year_id": str(fy.id), "group_by": ["item"],
                                   "txn_type": "ACTUAL_EXPENDITURE", "date_from": date_from, "date_to": date_to})
    wanted = {_code(c) for c in item_codes or []}
    rows = [{"item_code": r["g0"], "actual": r["total"], "entries": r["n"]} for r in rows
            if not wanted or r["g0"] in wanted]
    total = sum((Decimal(r["actual"]) for r in rows), Decimal("0"))
    return {**_source(fy, "مجموع المصروف الفعلي"), "date_from": date_from, "date_to": date_to,
            "by_item": rows, "total_actual": f"{total:.3f}", "link": "/reports"}


def list_transfers(ctx: ToolContext, fiscal_year: int | None = None, order_by: str = "amount",
                   limit: int = 10) -> dict:
    fy = _year(ctx, fiscal_year)
    rows, totals = _run(ctx, "RPT-05", {"fiscal_year_id": str(fy.id)})
    key = (lambda r: Decimal(r["amount"])) if order_by == "amount" else (lambda r: r["date"])
    rows = sorted(rows, key=key, reverse=True)[: max(1, min(limit, 50))]
    return {**_source(fy, "المناقلات"), "transfers": _mask(rows), "count_all": len(rows),
            "total_all": totals.get("amount"), "link": "/transfers"}


def list_pending_documents(ctx: ToolContext, doc_type: str | None = None) -> dict:
    fy = ctx.fiscal_year
    rows, totals = _run(ctx, "RPT-09", {"fiscal_year_id": str(fy.id)})
    if doc_type:
        rows = [r for r in rows if r["type"] == doc_type]
    return {**_source(fy, "العمليات المعلقة"), "documents": _mask(rows), "count": len(rows),
            "total_amount": totals.get("amount"), "link": "/approvals"}


def list_items_by_utilization(ctx: ToolContext, min_rate: float = 0, max_rate: float = 1000,
                              fiscal_year: int | None = None) -> dict:
    fy = _year(ctx, fiscal_year)
    rows, _ = _run(ctx, "RPT-07", {"fiscal_year_id": str(fy.id)})
    rows = [r for r in rows if r["utilization_rate"] is not None
            and Decimal(str(min_rate)) <= Decimal(r["utilization_rate"]) <= Decimal(str(max_rate))]
    rows.sort(key=lambda r: Decimal(r["utilization_rate"]), reverse=True)
    return {**_source(fy, "الرصيد المتاح ونسبة الاستخدام"), "items": rows, "count": len(rows), "link": "/budget"}


def list_overbudget_items(ctx: ToolContext, fiscal_year: int | None = None) -> dict:
    fy = _year(ctx, fiscal_year)
    rows, totals = _run(ctx, "RPT-08", {"fiscal_year_id": str(fy.id)})
    return {**_source(fy, "التجاوزات"), "items": rows, "count": len(rows), "total_excess": totals.get("excess"),
            "link": "/reports"}


def explain_variance(ctx: ToolContext, item_code: str, fiscal_year: int | None = None) -> dict:
    fy = _year(ctx, fiscal_year)
    pos = get_item_position(ctx, item_code, fy.year)
    moves, _ = _run(ctx, "RPT-11", {"fiscal_year_id": str(fy.id), "budget_line_id": str(_line_id(ctx, fy, item_code))})
    top = sorted(moves, key=lambda r: abs(Decimal(r["amount"])), reverse=True)[:8]
    return {**_source(fy, f"تفكيك الفرق للبند {_code(item_code)}"), "position": pos["position"],
            "largest_movements": _mask(top), "movement_count": len(moves), "link": pos["link"]}


def search_documents(ctx: ToolContext, query: str, doc_type: str | None = None) -> dict:
    from app.modules.search.service import search
    res = search(ctx.session, ctx.principal, query, types=[doc_type] if doc_type else None,
                 fiscal_year_id=ctx.fiscal_year.id, limit=20)
    docs = [{"type": d["type"], "doc_no": d["doc_no"], "date": d["date"].isoformat() if d["date"] else None,
             "amount": str(d["amount"]) if d["amount"] is not None else None, "status": d["status"],
             **({} if get_settings().ai_mask_personal else {"description": d["description"]})}
            for d in res["documents"]]
    return {**_source(ctx.fiscal_year, "البحث"), "documents": docs, "count": len(docs),
            "items": [{"code": i["code"], "name": i["name"]} for i in res["items"]], "link": f"/search?q={query}"}


def compare_years(ctx: ToolContext, years: list[int], item_code: str | None = None) -> dict:
    out = []
    for y in sorted(set(years))[:5]:
        fy = _year(ctx, y)
        rows, totals = _run(ctx, "RPT-18", {"fiscal_year_id": str(fy.id)})
        if item_code:
            r = next((x for x in rows if x["item_code"] == _code(item_code)), None)
            out.append({"year": y, **(r or {"item_code": _code(item_code), "budget": None, "actual": None})})
        else:
            out.append({"year": y, **totals})
    return {"source": "المقارنة بين السنوات", "as_of": date.today().isoformat(), "item_code": item_code,
            "years": out, "link": "/reports"}


def get_alerts(ctx: ToolContext, status: str = "OPEN", severity: str | None = None) -> dict:
    ctx.principal.require("alerts.view")
    stmt = select(Alert).where(Alert.fiscal_year_id == ctx.fiscal_year.id).order_by(Alert.created_at.desc()).limit(30)
    if status:
        stmt = stmt.where(Alert.status == status)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    alerts = [{"rule": a.rule_code, "severity": a.severity, "status": a.status, "message": a.message,
               "date": a.created_at.date().isoformat()} for a in ctx.session.scalars(stmt)]
    return {**_source(ctx.fiscal_year, "التنبيهات"), "alerts": alerts, "count": len(alerts), "link": "/alerts"}


def suggest_report(ctx: ToolContext, question: str) -> dict:
    return {"source": "دليل التقارير", "question": question, "link": "/reports",
            "reports": [{"code": d.code, "title": d.title, "description": d.description, "params": list(d.params)}
                        for d in REGISTRY.values() if ctx.principal.has(d.permission)]}


# ---------------------------------------------------------------------------
# تعريفات الأدوات للنموذج (strict: المدخلات تطابق المخطط تمامًا)
# ---------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    # strict يتطلب كل الحقول في required؛ الاختيارية تقبل null
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


YEAR = {"type": ["integer", "null"], "description": "السنة المالية مثل 2026؛ null = السنة المختارة"}
ITEM = {"type": "string", "description": "رمز البند مثل 2/18"}
DATE = {"type": ["string", "null"], "description": "تاريخ YYYY-MM-DD أو null"}

TOOLS: dict[str, tuple[Callable, str, dict]] = {
    "get_item_position": (get_item_position, "موقف بند واحد: الاعتماد والمفوض والمناقلات والفعلي والارتباطات والمتاح.",
                          _schema({"item_code": ITEM, "fiscal_year": YEAR, "as_of": DATE},
                                  ["item_code", "fiscal_year", "as_of"])),
    "sum_actual": (sum_actual, "مجموع المصروف الفعلي بين تاريخين، لكل بند وإجمالي. للأرباع استخدم حدود الربع.",
                   _schema({"fiscal_year": YEAR, "date_from": DATE, "date_to": DATE,
                            "item_codes": {"type": ["array", "null"], "items": {"type": "string"}}},
                           ["fiscal_year", "date_from", "date_to", "item_codes"])),
    "list_transfers": (list_transfers, "المناقلات مرتبة بالمبلغ أو بالتاريخ.",
                       _schema({"fiscal_year": YEAR, "order_by": {"type": "string", "enum": ["amount", "date"]},
                                "limit": {"type": "integer"}}, ["fiscal_year", "order_by", "limit"])),
    "list_pending_documents": (list_pending_documents, "المستندات التي تنتظر الموافقة ومرحلتها وأيام الانتظار.",
                               _schema({"doc_type": {"type": ["string", "null"],
                                                     "description": "مثل مصروف أو مناقلة؛ null = الكل"}}, ["doc_type"])),
    "list_items_by_utilization": (list_items_by_utilization, "البنود التي تقع نسبة استخدامها بين حدين (بالمئة).",
                                  _schema({"min_rate": {"type": "number"}, "max_rate": {"type": "number"},
                                           "fiscal_year": YEAR}, ["min_rate", "max_rate", "fiscal_year"])),
    "list_overbudget_items": (list_overbudget_items, "البنود المتجاوزة للاعتماد ومقدار التجاوز.",
                              _schema({"fiscal_year": YEAR}, ["fiscal_year"])),
    "explain_variance": (explain_variance, "سبب الفرق بين الاعتماد والمصروف لبند: مكونات الرصيد وأكبر الحركات.",
                         _schema({"item_code": ITEM, "fiscal_year": YEAR}, ["item_code", "fiscal_year"])),
    "search_documents": (search_documents, "بحث في المستندات برقم أو مبلغ أو رمز بند.",
                         _schema({"query": {"type": "string"},
                                  "doc_type": {"type": ["string", "null"],
                                               "enum": ["budget_document", "authorization", "transfer", "commitment",
                                                        "expenditure", "adjustment", None]}},
                                 ["query", "doc_type"])),
    "compare_years": (compare_years, "مقارنة الاعتماد والفعلي بين سنوات، إجمالًا أو لبند.",
                      _schema({"years": {"type": "array", "items": {"type": "integer"}},
                               "item_code": {"type": ["string", "null"]}}, ["years", "item_code"])),
    "get_alerts": (get_alerts, "التنبيهات الرقابية للسنة المختارة.",
                   _schema({"status": {"type": "string", "enum": ["OPEN", "ACKNOWLEDGED", "RESOLVED"]},
                            "severity": {"type": ["string", "null"],
                                         "enum": ["INFO", "WARNING", "HIGH", "CRITICAL", None]}},
                           ["status", "severity"])),
    "suggest_report": (suggest_report, "قائمة التقارير المتاحة للمستخدم لاقتراح التقرير المناسب ومعاملاته.",
                       _schema({"question": {"type": "string"}}, ["question"])),
}


def definitions_for_model() -> list[dict]:
    return [{"name": n, "description": desc, "input_schema": schema, "strict": True}
            for n, (_, desc, schema) in TOOLS.items()]


def call(ctx: ToolContext, name: str, args: dict) -> dict:
    if name not in TOOLS:
        raise ValidationFailed(f"أداة غير معروفة: {name}", code="UNKNOWN_TOOL")
    fn = TOOLS[name][0]
    return fn(ctx, **{k: v for k, v in args.items() if v is not None})
