"""مسار الاستيراد (10-excel-import §1): رفع ← تحليل ← ربط ← تحقق ← تكرار ← قيم غير صالحة ← معاينة
وقرارات ← استيراد ← تقرير. لا استيراد أعمى: كل رقم يُعاد حسابه من الحركات، وقيم Excel للمقارنة فقط."""
import difflib
import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.modules.attachments import service as attachments
from app.modules.catalog.models import BudgetItem, Entity, Supplier
from app.modules.catalog.seed import DEFAULT_ENTITY
from app.modules.fiscal.models import FiscalYear
from app.modules.imports.models import ImportBatch, ImportIssue, ImportRow
from app.modules.imports.parser import ParsedRow, parse_period, parse_workbook
from app.shared.arabic import normalize, normalize_party_name
from app.shared.money import fmt

ZERO = Decimal("0")
MEANINGFUL = ("AUTHORIZATION_ALLOCATION", "TRANSFER_OUT", "TRANSFER_IN_AGGREGATE", "ACTUAL_EXPENDITURE", "COMMITMENT")
DEFAULT_MAPPING = {"date": "A", "description": "B", "document_no": "C", "amount": "D", "actual": "E",
                   "commitment": "F", "notes": "I", "excel_cumulative": "G", "excel_balance": "H"}


def get(session: Session, batch_id: uuid.UUID) -> ImportBatch:
    b = session.get(ImportBatch, batch_id)
    if b is None:
        raise NotFound("دفعة الاستيراد غير موجودة.")
    return b


def upload(session: Session, p: Principal, filename: str, data: bytes, fiscal_year_id: uuid.UUID,
           entity_id: uuid.UUID | None) -> ImportBatch:
    if not filename.lower().endswith(".xlsx"):
        raise ValidationFailed("الاستيراد يقبل ملفات xlsx فقط.", code="FILE_TYPE_NOT_ALLOWED")
    digest = hashlib.sha256(data).hexdigest()
    if session.scalar(select(ImportBatch.id).where(ImportBatch.sha256 == digest, ImportBatch.status == "IMPORTED")):
        raise Conflict("هذا الملف (بالبصمة نفسها) استُورد مسبقًا.", code="ALREADY_IMPORTED")
    fy = session.get(FiscalYear, fiscal_year_id)
    if fy is None:
        raise ValidationFailed("السنة المالية غير موجودة.", code="UNKNOWN_YEAR")
    p.require_scope("FISCAL_YEAR", fy.id)
    if entity_id is None:
        entity_id = session.scalar(select(Entity.id).where(Entity.code == DEFAULT_ENTITY[0]))
    att = attachments.save(session, filename=filename, data=data, uploaded_by=p.user_id, category="IMPORT_SOURCE",
                           description="ملف مصدر للاستيراد")
    att.is_locked = True
    b = ImportBatch(id=uuid.uuid4(), filename=att.original_filename, sha256=digest, attachment_id=att.id,
                    fiscal_year_id=fy.id, entity_id=entity_id, status="UPLOADED", created_by=p.user_id,
                    mapping=DEFAULT_MAPPING, decisions={})
    session.add(b)
    session.flush()
    return b


def _source_bytes(session: Session, b: ImportBatch) -> bytes:
    return attachments.read_bytes(attachments.get(session, b.attachment_id))


def analyze(session: Session, b: ImportBatch) -> dict:
    if b.status not in ("UPLOADED", "ANALYZED", "VALIDATED"):
        raise Conflict("لا يمكن إعادة تحليل دفعة منتهية.", code="INVALID_STATE")
    wb = parse_workbook(_source_bytes(session, b))
    session.execute(delete(ImportIssue).where(ImportIssue.batch_id == b.id))
    session.execute(delete(ImportRow).where(ImportRow.batch_id == b.id))
    counts: dict[str, int] = defaultdict(int)
    sheets = []
    for s in wb.sheets:
        sheets.append({"name": s.name, "item_code": s.item_code, "year": s.year, "sector": s.sector,
                       "rows": len(s.rows), "constant_formula_cells": s.constant_formula_cells,
                       "excel_last_g": str(s.last_excel_g) if s.last_excel_g is not None else None,
                       "excel_last_h": str(s.last_excel_h) if s.last_excel_h is not None else None})
        for r in s.rows:
            counts[r.classification] += 1
            session.add(ImportRow(batch_id=b.id, sheet_name=s.name, row_no=r.row_no, raw=r.as_json(),
                                  parsed={"item_code": s.item_code, "year": s.year}, classification=r.classification,
                                  excel_computed={"G": r.excel_g, "H": r.excel_h}))
    summary = None
    if wb.summary:
        summary = {"name": wb.summary.name, "total_formula": wb.summary.total_formula,
                   "total_value": str(wb.summary.total_value) if wb.summary.total_value is not None else None,
                   "total_cell": wb.summary.total_cell, "header_labels": wb.summary.header_labels,
                   "lines": [{"row": ln.row_no, "item_code": ln.item_code, "label": ln.label, "formula": ln.formula,
                              "value": str(ln.value) if ln.value is not None else None} for ln in wb.summary.lines]}
    b.stats = {"sheets": sheets, "summary": summary, "classification_counts": dict(counts),
               "analyzed_at": datetime.now(UTC).isoformat()}
    b.status = "ANALYZED"
    session.flush()
    return b.stats


# ---------------------------------------------------------------------------
# الخطة: ما سيُنشأ من مستندات، والأرصدة المعاد حسابها
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    authorizations: dict[tuple[str, str], dict] = field(default_factory=dict)
    transfers: list[dict] = field(default_factory=list)
    expenditures: list[dict] = field(default_factory=list)
    commitments: list[dict] = field(default_factory=list)
    positions: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    transfer_in_declared: dict[str, Decimal] = field(default_factory=dict)
    estimated_dates: int = 0


def _rows(session: Session, b: ImportBatch) -> list[tuple[ImportRow, ParsedRow]]:
    out = []
    for ir in session.scalars(select(ImportRow).where(ImportRow.batch_id == b.id)
                              .order_by(ImportRow.sheet_name, ImportRow.row_no)):
        raw = dict(ir.raw)
        for k in ("amount", "actual", "commitment"):
            raw[k] = Decimal(raw[k])
        out.append((ir, ParsedRow(**raw)))
    return out


def _row_date(b: ImportBatch, fy: FiscalYear, pr: ParsedRow) -> tuple[date, bool]:
    """D-04: تاريخ الصف إن وُجد، وإلا تاريخ من قرارات المعاينة، وإلا التاريخ الافتراضي موسومًا «تقديري»."""
    key = f"{pr.sheet}!{pr.row_no}"
    overrides = (b.decisions or {}).get("row_dates", {})
    if key in overrides:
        return date.fromisoformat(overrides[key]), False
    if pr.date:
        return date.fromisoformat(pr.date), False
    default = (b.decisions or {}).get("default_date")
    return (date.fromisoformat(default) if default else fy.end_date), True


def build_plan(session: Session, b: ImportBatch) -> Plan:
    fy = session.get(FiscalYear, b.fiscal_year_id)
    plan = Plan()
    pos: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    auth_amounts = (b.decisions or {}).get("authorization_amounts", {})
    for ir, pr in _rows(session, b):
        item = (ir.parsed or {}).get("item_code")
        if pr.classification not in MEANINGFUL or item is None:
            continue
        d, estimated = _row_date(b, fy, pr)
        plan.estimated_dates += int(estimated)
        ref = f"{pr.sheet}!{pr.row_no}"
        if pr.classification == "AUTHORIZATION_ALLOCATION":
            key = (pr.auth_type, pr.auth_no or f"بلا-رقم-{ref}")
            a = plan.authorizations.setdefault(key, {"auth_type": key[0], "auth_no": key[1], "allocations": {},
                                                     "rows": [], "period_text": pr.period_text, "date": d,
                                                     "estimated": estimated, "descriptions": set()})
            a["allocations"][item] = a["allocations"].get(item, ZERO) + pr.amount
            a["rows"].append(ir.id)
            a["descriptions"].add(pr.description)
            a["date"] = min(a["date"], d)
            a["estimated"] = a["estimated"] and estimated
            pos[item]["allocation"] += pr.amount
        elif pr.classification == "TRANSFER_OUT":
            plan.transfers.append({"from": item, "to": pr.target_item, "amount": -pr.amount, "row": ir.id, "ref": ref,
                                   "date": d, "estimated": estimated, "reason": pr.description, "doc": pr.doc})
            pos[item]["transfer_out"] += -pr.amount
            if pr.target_item:
                pos[pr.target_item]["transfer_in"] += -pr.amount
        elif pr.classification == "TRANSFER_IN_AGGREGATE":
            plan.transfer_in_declared[item] = plan.transfer_in_declared.get(item, ZERO) + pr.amount
        elif pr.classification == "ACTUAL_EXPENDITURE":
            plan.expenditures.append({"item": item, "amount": pr.actual, "doc": pr.doc, "row": ir.id, "ref": ref,
                                      "date": d, "estimated": estimated, "beneficiary": pr.description,
                                      "notes": pr.notes, "deposit": pr.deposit_account})
            pos[item]["actual"] += pr.actual
        elif pr.classification == "COMMITMENT":
            plan.commitments.append({"item": item, "amount": pr.commitment, "doc": pr.doc, "row": ir.id, "ref": ref,
                                     "date": d, "estimated": estimated, "description": pr.description})
            pos[item]["commitment"] += pr.commitment
    for key, a in plan.authorizations.items():
        declared = auth_amounts.get(f"{key[0]}:{key[1]}")
        a["amount"] = Decimal(declared) if declared else sum(a["allocations"].values(), ZERO)
        a["amount_confirmed"] = declared is not None
    for vals in pos.values():
        vals["book_balance"] = vals["allocation"] + vals["transfer_in"] - vals["transfer_out"] - vals["actual"]
        vals["available"] = vals["book_balance"] - vals["commitment"]
    plan.positions = {k: dict(v) for k, v in pos.items()}
    return plan


# ---------------------------------------------------------------------------
# قواعد جودة البيانات (10-excel-import §4)
# ---------------------------------------------------------------------------
def _issue(session: Session, b: ImportBatch, code: str, severity: str, message: str, *, location: str | None = None,
           row_id=None, decision: str | None = None, blocking: bool = False, details: dict | None = None) -> None:
    session.add(ImportIssue(batch_id=b.id, row_id=row_id, severity=severity, code=code, location=location,
                            message=message, decision=decision, blocking=blocking,
                            details={k: (str(v) if isinstance(v, Decimal) else v) for k, v in (details or {}).items()}))


def validate(session: Session, b: ImportBatch) -> dict:
    if b.status not in ("ANALYZED", "VALIDATED"):
        raise Conflict("حلل الملف أولًا.", code="INVALID_STATE")
    session.execute(delete(ImportIssue).where(ImportIssue.batch_id == b.id))
    fy = session.get(FiscalYear, b.fiscal_year_id)
    catalog = {i.code: i for i in session.scalars(select(BudgetItem))}
    sheets = {s["name"]: s for s in b.stats["sheets"]}
    rows = _rows(session, b)
    plan = build_plan(session, b)

    for s in sheets.values():
        if not s["item_code"]:
            _issue(session, b, "IMP-00", "CRITICAL", f"الورقة «{s['name']}» بلا رأس بند (البند : …)",
                   location=s["name"], blocking=True)
        elif s["item_code"] not in catalog:
            _issue(session, b, "IMP-03", "CRITICAL", f"البند {s['item_code']} غير موجود في الدليل", location=s["name"],
                   blocking=True)
        elif not catalog[s["item_code"]].is_postable or not catalog[s["item_code"]].is_active:
            _issue(session, b, "IMP-03", "CRITICAL", f"البند {s['item_code']} غير نشط أو غير قابل للترحيل",
                   location=s["name"], blocking=True)
        if s["year"] and s["year"] != fy.year:
            _issue(session, b, "IMP-04", "WARNING", f"سنة الورقة {s['year']} تختلف عن سنة الدفعة {fy.year}",
                   location=s["name"])
        for cell in s["constant_formula_cells"]:
            _issue(session, b, "IMP-07", "HIGH", "قيمة ثابتة مكان معادلة داخل عمود المعادلات", location=f"{s['name']}!{cell}")

    targets_unknown = {t["to"] for t in plan.transfers if t["to"] and t["to"] not in catalog}
    for ir, pr in rows:
        loc = f"{pr.sheet}!{pr.row_no}"
        if pr.classification == "UNCLASSIFIED":
            _issue(session, b, "IMP-UNC", "CRITICAL", f"صف لم يُصنَّف: «{pr.description}» (مبلغ {pr.amount}، فعلي {pr.actual})",
                   location=loc, row_id=ir.id, blocking=True)
        if pr.classification == "TRANSFER_OUT" and not pr.target_item:
            _issue(session, b, "IMP-TRG", "CRITICAL", "مناقلة صادرة دون بند مستفيد واضح", location=loc, row_id=ir.id,
                   blocking=True)
        if pr.amount < 0 and pr.classification != "TRANSFER_OUT":
            _issue(session, b, "IMP-02", "HIGH", "مبلغ سالب غير مبرر", location=loc, row_id=ir.id)
        if pr.classification in MEANINGFUL and pr.doc and not re.fullmatch(r"\d+", pr.doc):
            _issue(session, b, "IMP-14", "INFO", f"رقم مستند غير رقمي «{pr.doc}»", location=loc, row_id=ir.id)
        if pr.period_text and pr.classification == "AUTHORIZATION_ALLOCATION":
            years = {int(y) for y in re.findall(r"(20\d{2})", pr.description)}
            if years and years != {fy.year}:
                _issue(session, b, "IMP-04", "WARNING",
                       f"تفويض بفترة في سنة {', '.join(map(str, sorted(years)))} داخل سجل {fy.year}؛ يُستورد على سنة الدفعة"
                       " دون فترة (D-06)", location=loc, row_id=ir.id, decision="D-06")
            elif parse_period(pr.period_text, fy.year) == (None, None):
                _issue(session, b, "IMP-PER", "INFO", f"فترة التفويض «{pr.period_text}» غامضة؛ تُحفظ نصًا فقط",
                       location=loc, row_id=ir.id)
        if pr.notes and re.fullmatch(r"\d+\s*[-/]+\s*\d+", pr.notes):
            _issue(session, b, "IMP-NOTE", "INFO", f"ملاحظة غامضة «{pr.notes}» تُستورد نصًا (D-05)", location=loc,
                   row_id=ir.id, decision="D-05")
        if pr.classification == "ACTUAL_EXPENDITURE" and pr.deposit_account:
            _issue(session, b, "IMP-DEP", "INFO", "دفع لحساب أمانات: يُستورد مصروفًا فعليًا بطريقة «حساب أمانات» (D-07)",
                   location=loc, row_id=ir.id, decision="D-07")
    for t in sorted(targets_unknown):
        _issue(session, b, "IMP-03", "CRITICAL", f"بند المناقلة المستفيد {t} غير موجود", blocking=True)

    # IMP-12 التواريخ المفقودة
    meaningful = [pr for _, pr in rows if pr.classification in MEANINGFUL]
    undated = [pr for pr in meaningful if not pr.date]
    if undated:
        pct = round(100 * len(undated) / max(len(meaningful), 1))
        _issue(session, b, "IMP-12", "HIGH", f"{len(undated)} صفًا ({pct}%) بلا تاريخ؛ تأخذ التاريخ الافتراضي موسومًا «تقديري»"
               " ما لم يُدخل تاريخها في المعاينة (D-04)", decision="D-04", details={"count": len(undated)})

    # IMP-13 ترتيب مشكوك: مصروف قبل صف التفويض في الورقة نفسها
    by_sheet: dict[str, list[ParsedRow]] = defaultdict(list)
    for _, pr in rows:
        by_sheet[pr.sheet].append(pr)
    for sheet, prs in by_sheet.items():
        firsts = [i for i, pr in enumerate(prs) if pr.classification == "AUTHORIZATION_ALLOCATION"]
        if firsts and any(pr.classification == "ACTUAL_EXPENDITURE" for pr in prs[:firsts[0]]):
            _issue(session, b, "IMP-13", "WARNING", "صفوف مصروف تسبق صف التفويض في الورقة", location=sheet)

    # IMP-01 أرقام مستندات مكررة بين الأوراق
    docs: dict[str, list[str]] = defaultdict(list)
    for e in plan.expenditures:
        if e["doc"]:
            docs[e["doc"]].append(e["ref"])
    dups = {k: v for k, v in docs.items() if len(v) > 1}
    if dups:
        _issue(session, b, "IMP-01", "WARNING", f"{len(dups)} رقم مستند يتكرر عبر الأوراق؛ يُستورد بمفتاح (البند-الرقم)"
               " ويُحفظ الأصل مرجعًا", details={"examples": dict(list(dups.items())[:5])})

    # IMP-08 قيمة التفويض غير معلنة
    for (atype, ano), a in plan.authorizations.items():
        if not a["amount_confirmed"]:
            _issue(session, b, "IMP-08", "WARNING", f"قيمة التفويض {ano} غير مكتوبة في الملف؛ تُعتمد مجموع توزيعاته"
                   f" {fmt(a['amount'])} ما لم تُصحَّح في القرارات", decision="AUTH_AMOUNT",
                   details={"key": f"{atype}:{ano}", "sum": a["amount"], "items": len(a["allocations"])})

    # IMP-09/10 أرصدة سالبة وصرف بلا تفويض — وIMP-06 مقارنة Excel
    for item, v in sorted(plan.positions.items()):
        sheet = next((s for s in sheets.values() if s["item_code"] == item), None)
        if v["available"] < 0:
            _issue(session, b, "IMP-09", "HIGH", f"البند {item}: الرصيد المعاد حسابه {fmt(v['available'])} (سالب)",
                   location=sheet["name"] if sheet else None, decision="D-02", details={"available": v["available"]})
        if v.get("actual", ZERO) > 0 and v.get("allocation", ZERO) == 0 and v.get("transfer_in", ZERO) == 0:
            _issue(session, b, "IMP-10", "HIGH", f"البند {item}: صرف {fmt(v['actual'])} دون أي تفويض",
                   location=sheet["name"] if sheet else None, decision="D-02")
        if sheet and sheet["excel_last_h"] is not None:
            excel_h = Decimal(sheet["excel_last_h"])
            if excel_h != v["book_balance"]:
                _issue(session, b, "IMP-06", "CRITICAL",
                       f"البند {item}: رصيد Excel {fmt(excel_h)} ≠ الرصيد المعاد حسابه {fmt(v['book_balance'])}",
                       location=sheet["name"], details={"excel": excel_h, "recomputed": v["book_balance"]})

    # IMP-11 مناقلات غير متوازنة
    outs_to: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in plan.transfers:
        if t["to"]:
            outs_to[t["to"]] += t["amount"]
    for item in set(outs_to) | set(plan.transfer_in_declared):
        declared = plan.transfer_in_declared.get(item, ZERO)
        if declared != outs_to[item]:
            _issue(session, b, "IMP-11", "CRITICAL",
                   f"البند {item}: الوارد المسجل في Excel {fmt(declared)} ≠ مجموع الصادر إليه {fmt(outs_to[item])}"
                   f" (فرق {fmt(outs_to[item] - declared)})؛ تُستورد مناقلة مستقلة لكل صف صادر (D-03)",
                   decision="D-03", details={"declared_in": declared, "sum_out": outs_to[item]})

    # الملخص: IMP-05 وIMP-15 وIMP-16 وDQ-04
    summ = b.stats.get("summary")
    if summ:
        total_actual = sum((v.get("actual", ZERO) for v in plan.positions.values()), ZERO)
        if summ["total_value"] is not None and Decimal(summ["total_value"]) != total_actual:
            _issue(session, b, "IMP-05", "CRITICAL",
                   f"إجمالي الملخص {fmt(Decimal(summ['total_value']))} ≠ مجموع المصروفات المعاد حسابه {fmt(total_actual)}"
                   f" (فرق {fmt(total_actual - Decimal(summ['total_value']))})", location=f"{summ['name']}!{summ['total_cell']}")
        m = re.search(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", summ["total_formula"] or "")
        if m and summ["lines"]:
            lo, hi = int(m.group(2)), int(m.group(4))
            missing = [ln for ln in summ["lines"] if not lo <= ln["row"] <= hi]
            for ln in missing:
                _issue(session, b, "IMP-15", "CRITICAL", f"البند {ln['item_code']} (الصف {ln['row']}) خارج نطاق {summ['total_formula']}",
                       location=f"{summ['name']}!{summ['total_cell']}")
        if any("الرصيد" in h for h in summ["header_labels"]) and all(
                (ln["formula"] or "").rstrip().split("!")[-1].startswith("G") for ln in summ["lines"] if ln["formula"]):
            _issue(session, b, "DQ-04", "HIGH", "عمود الملخص المعنون «الرصيد» يسحب المصروفات (العمود G) وليس الرصيد",
                   location=summ["name"])
        for ln in summ["lines"]:
            it = catalog.get(ln["item_code"])
            label, name = normalize(ln["label"]), normalize(it.name)
            similar = label in name or name in label or difflib.SequenceMatcher(None, label, name).ratio() >= 0.5
            if it and label and not similar:
                _issue(session, b, "IMP-16", "INFO", f"تسمية الملخص «{ln['label']}» تختلف عن اسم البند {it.code} «{it.name}»",
                       location=f"{summ['name']}!D{ln['row']}")

    # IMP-17 موردون بأسماء متشابهة
    names = sorted({normalize_party_name(e["beneficiary"]) for e in plan.expenditures if e["beneficiary"]})
    for i, a in enumerate(names):
        for c in names[i + 1:]:
            if difflib.SequenceMatcher(None, a, c).ratio() >= 0.85:
                _issue(session, b, "IMP-17", "INFO", f"اسمان متشابهان لمستفيد: «{a}» و«{c}»؛ يُقترح الدمج بعد التأكد")

    session.flush()
    b.status = "VALIDATED"
    counts = defaultdict(int)
    for sev, in session.execute(select(ImportIssue.severity).where(ImportIssue.batch_id == b.id)):
        counts[sev] += 1
    blocking = session.scalar(select(ImportIssue.id).where(ImportIssue.batch_id == b.id, ImportIssue.blocking.is_(True)).limit(1))
    b.stats = {**b.stats, "issues": dict(counts), "blocking": blocking is not None,
               "validated_at": datetime.now(UTC).isoformat()}
    session.flush()
    return b.stats


DECISION_KEYS = {"default_date", "row_dates", "authorization_amounts", "accept_historical_exceptions"}


def set_decisions(session: Session, b: ImportBatch, decisions: dict) -> dict:
    if b.status not in ("ANALYZED", "VALIDATED"):
        raise Conflict("لا يمكن تعديل قرارات دفعة منتهية.", code="INVALID_STATE")
    unknown = set(decisions) - DECISION_KEYS
    if unknown:
        raise ValidationFailed("قرارات غير معروفة.", code="UNKNOWN_DECISION", details={"keys": sorted(unknown)})
    fy = session.get(FiscalYear, b.fiscal_year_id)
    try:
        if decisions.get("default_date"):
            d = date.fromisoformat(decisions["default_date"])
            if not fy.start_date <= d <= fy.end_date:
                raise ValidationFailed("التاريخ الافتراضي خارج السنة المالية.", code="DATE_OUTSIDE_YEAR")
        for v in (decisions.get("row_dates") or {}).values():
            if not fy.start_date <= date.fromisoformat(v) <= fy.end_date:
                raise ValidationFailed(f"التاريخ {v} خارج السنة المالية.", code="DATE_OUTSIDE_YEAR")
        for v in (decisions.get("authorization_amounts") or {}).values():
            if Decimal(str(v)) <= 0:
                raise ValidationFailed("قيمة التفويض يجب أن تكون موجبة.", code="INVALID_AMOUNT")
    except ValueError as e:
        raise ValidationFailed("قيمة قرار غير صالحة.", code="INVALID_DECISION") from e
    b.decisions = {**(b.decisions or {}), **decisions}
    b.status = "ANALYZED"   # أي قرار جديد يتطلب إعادة التحقق
    session.flush()
    return b.decisions


def preview(session: Session, b: ImportBatch) -> dict:
    plan = build_plan(session, b)
    sheets = {s["item_code"]: s for s in b.stats["sheets"]}
    items = []
    for item, v in sorted(plan.positions.items(), key=lambda kv: [int(x) for x in kv[0].split("/")]):
        s = sheets.get(item) or {}
        excel_h = Decimal(s["excel_last_h"]) if s.get("excel_last_h") is not None else None
        items.append({"item_code": item, **{k: f"{val:.3f}" for k, val in v.items()},
                      "excel_balance": f"{excel_h:.3f}" if excel_h is not None else None,
                      "difference": f"{v['book_balance'] - excel_h:.3f}" if excel_h is not None else None})
    return {
        "authorizations": [{"auth_type": a["auth_type"], "auth_no": a["auth_no"], "amount": str(a["amount"]),
                            "amount_confirmed": a["amount_confirmed"], "allocations": {k: str(x) for k, x in a["allocations"].items()},
                            "period_text": a["period_text"]} for a in plan.authorizations.values()],
        "transfers": [{"from": t["from"], "to": t["to"], "amount": str(t["amount"]), "ref": t["ref"]} for t in plan.transfers],
        "expenditures": [{"item": e["item"], "amount": str(e["amount"]), "doc": e["doc"], "beneficiary": e["beneficiary"],
                          "ref": e["ref"], "deposit": e["deposit"], "date": e["date"].isoformat(),
                          "estimated_date": e["estimated"]} for e in plan.expenditures],
        "commitments": [{"item": c["item"], "amount": str(c["amount"]), "ref": c["ref"]} for c in plan.commitments],
        "positions": items,
        "totals": {k: f"{sum((Decimal(i[k]) for i in items), ZERO):.3f}" for k in
                   ("allocation", "transfer_in", "transfer_out", "actual", "book_balance")},
        "estimated_dates": plan.estimated_dates,
        "decisions": b.decisions,
    }


# ---------------------------------------------------------------------------
# الاستيراد الفعلي (معاملة واحدة، عبر محرك الترحيل نفسه)
# ---------------------------------------------------------------------------
def _supplier(session: Session, name: str, deposit: bool) -> uuid.UUID | None:
    from app.modules.catalog.service import create_supplier
    norm = normalize_party_name(name)
    if not norm:
        return None
    existing = session.scalar(select(Supplier.id).where(Supplier.name_normalized == norm))
    if existing:
        return existing
    kind = "GOV_ACCOUNT" if deposit else ("PERSON" if name.strip().startswith("السيد") else "COMPANY")
    return create_supplier(session, name=name, kind=kind, tax_no=None, commercial_reg=None, phone=None,
                           notes="أُنشئ آليًا من الاستيراد", allow_similar=True).id


def commit(session: Session, p: Principal, b: ImportBatch) -> dict:
    from app.modules.authorizations import service as auths
    from app.modules.commitments import service as cms
    from app.modules.expenditures import service as exps
    from app.modules.transfers import service as trs

    if b.status != "VALIDATED":
        raise Conflict("يجب التحقق من الدفعة (بعد آخر قرار) قبل الاستيراد.", code="NOT_VALIDATED")
    if b.created_by == p.user_id:
        raise Forbidden("ينفذ الاستيراد مستخدم غير الذي حضّر الدفعة (فصل المهام).", code="SEGREGATION_OF_DUTIES")
    if b.stats.get("blocking"):
        raise Conflict("توجد مشاكل مانعة يجب حلها أولًا.", code="BLOCKING_ISSUES")
    fy = session.get(FiscalYear, b.fiscal_year_id)
    if fy.status != "OPEN":
        raise Conflict(f"السنة المالية {fy.year} ليست مفتوحة.", code="YEAR_NOT_OPEN")
    needs_exception = session.scalar(select(ImportIssue.id).where(ImportIssue.batch_id == b.id,
                                                                  ImportIssue.decision == "D-02").limit(1))
    if needs_exception and not (b.decisions or {}).get("accept_historical_exceptions"):
        raise Conflict("الملف يحتوي أرصدة سالبة أو صرفًا بلا تفويض؛ يجب قبول استيرادها كاستثناءات تاريخية (D-02).",
                       code="HISTORICAL_EXCEPTIONS_NOT_ACCEPTED")
    plan = build_plan(session, b)
    items = {i.code: i.id for i in session.scalars(select(BudgetItem))}
    entity = b.entity_id
    created = defaultdict(int)
    row_targets: dict[uuid.UUID, tuple[str, uuid.UUID]] = {}

    for (atype, ano), a in plan.authorizations.items():
        pf, pt = parse_period(a["period_text"], fy.year)
        purpose = "؛ ".join(sorted(a["descriptions"])) + " (مستورد من سجل Excel)"
        auth = auths.create_draft(session, p, fiscal_year_id=fy.id, auth_no=ano, auth_type=atype, auth_date=a["date"],
                                  entity_id=entity, period_from=pf, period_to=pt, amount=a["amount"],
                                  purpose=purpose[:2000], funding_source_id=None, over_allocation_reason="مستورد",
                                  allocations=[{"item_id": items[i], "amount": v} for i, v in a["allocations"].items()])
        auth.legacy_ref = f"batch:{b.id}"
        auth.is_historical_exception = True
        auths.post(session, auth, p.user_id, historical_exception=True, date_is_estimated=a["estimated"])
        created["authorizations"] += 1
        for rid in a["rows"]:
            row_targets[rid] = ("authorization", auth.id)

    for t in plan.transfers:
        tr = trs.create_draft(session, p, fiscal_year_id=fy.id, transfer_date=t["date"], reason=t["reason"],
                              approval_no=t["doc"], lines=[{"from_entity_id": entity, "from_item_id": items[t["from"]],
                                                            "to_entity_id": entity, "to_item_id": items[t["to"]],
                                                            "amount": t["amount"]}])
        tr.legacy_ref = t["ref"]
        tr.is_historical_exception = True
        trs.post(session, tr, p.user_id, historical_exception=True, date_is_estimated=t["estimated"])
        created["transfers"] += 1
        row_targets[t["row"]] = ("transfer", tr.id)

    for c in plan.commitments:
        cm = cms.create_draft(session, p, fiscal_year_id=fy.id, commitment_type="OBLIGATION", commitment_date=c["date"],
                              entity_id=entity, item_id=items[c["item"]], amount=c["amount"],
                              description=c["description"] or "ارتباط مستورد")
        cm.legacy_ref = c["ref"]
        cms.post(session, cm, p.user_id, historical_exception=True, date_is_estimated=c["estimated"])
        created["commitments"] += 1
        row_targets[c["row"]] = ("commitment", cm.id)

    for e in plan.expenditures:
        doc_no = f"{e['item']}-{e['doc'] or e['ref'].split('!')[-1]}"
        ex = exps.create_draft(session, p, fiscal_year_id=fy.id, expenditure_date=e["date"], entity_id=entity,
                               item_id=items[e["item"]], amount=e["amount"],
                               payment_method="DEPOSIT_ACCOUNT" if e["deposit"] else "OTHER",
                               description=e["beneficiary"] or "مصروف مستورد", document_no=doc_no,
                               document_type="LEGACY_VOUCHER", supplier_id=_supplier(session, e["beneficiary"], e["deposit"]),
                               notes=e["notes"])
        ex.legacy_ref = e["ref"]
        ex.date_is_estimated = e["estimated"]
        ex.is_historical_exception = True
        exps.post(session, ex, p.user_id, historical_exception=True)
        created["expenditures"] += 1
        row_targets[e["row"]] = ("expenditure", ex.id)

    for ir in session.scalars(select(ImportRow).where(ImportRow.batch_id == b.id)):
        if ir.id in row_targets:
            ir.target_type, ir.target_id = row_targets[ir.id]
            ir.status = "IMPORTED"
        else:
            ir.status = "SKIPPED"
    b.status, b.imported_by, b.imported_at = "IMPORTED", p.user_id, datetime.now(UTC)
    b.stats = {**b.stats, "created": dict(created)}
    session.flush()
    from app.modules.alerts.service import run_periodic
    run_periodic(session)
    return dict(created)


def discard(session: Session, b: ImportBatch) -> None:
    if b.status == "IMPORTED":
        raise Conflict("الدفعة مستوردة؛ التصحيح يكون بقيود عكسية للمستندات المعنية.", code="ALREADY_IMPORTED")
    b.status = "DISCARDED"
    session.flush()


def issues(session: Session, b: ImportBatch) -> list[ImportIssue]:
    order = {"CRITICAL": 0, "HIGH": 1, "WARNING": 2, "INFO": 3}
    rows = list(session.scalars(select(ImportIssue).where(ImportIssue.batch_id == b.id)))
    return sorted(rows, key=lambda i: (order[i.severity], i.code, i.location or ""))
