"""منطق الأعمال: أرصدة الاعتمادات، التحقق من المصروفات، والتنبيهات.

قواعد النظام:
- الاعتماد الحالي = الاعتماد الأصلي + التعزيزات - التخفيضات.
- المصروفات قيد الاعتماد تُحجز من الرصيد فور تسجيلها حتى لا يتم تجاوز الاعتماد.
- الرصيد المتاح = الاعتماد الحالي - المصروف الفعلي (المعتمد) - المحجوز (قيد الاعتماد).
- لا يُسمح بصرف أو تخفيض أو مناقلة تتجاوز الرصيد المتاح.
"""
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

WARNING_THRESHOLD = 80  # نسبة الصرف التي يبدأ عندها التحذير
CRITICAL_THRESHOLD = 95  # نسبة الصرف الحرجة

STATUS_LABELS = {
    "pending": "قيد الاعتماد",
    "approved": "معتمد",
    "rejected": "مرفوض",
}


class ValidationError(Exception):
    """خطأ في صحة البيانات المدخلة أو مخالفة لقواعد الصرف."""


# ---------------------------------------------------------------------------
# تحويل المبالغ
# ---------------------------------------------------------------------------

def parse_amount(value):
    """تحويل نص المبلغ إلى عدد صحيح بأصغر وحدة نقدية (×100)."""
    if value is None or str(value).strip() == "":
        raise ValidationError("المبلغ مطلوب.")
    text = str(value).strip().replace(",", "").replace("٬", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValidationError("المبلغ غير صالح.")
    if not amount.is_finite():
        raise ValidationError("المبلغ غير صالح.")
    if amount.as_tuple().exponent < -2:
        raise ValidationError("المبلغ يقبل خانتين عشريتين كحد أقصى.")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def format_amount(minor_units):
    """عرض المبلغ بصيغة مقروءة مع فواصل الآلاف."""
    value = Decimal(minor_units or 0) / 100
    return f"{value:,.2f}"


def _parse_date(value):
    if not value:
        return date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValidationError("التاريخ غير صالح.")


def _required(value, label):
    value = (value or "").strip()
    if not value:
        raise ValidationError(f"حقل {label} مطلوب.")
    return value


# ---------------------------------------------------------------------------
# الجهات والبنود
# ---------------------------------------------------------------------------

def create_entity(db, code, name):
    code, name = _required(code, "الرمز"), _required(name, "اسم الجهة")
    if db.execute("SELECT 1 FROM entities WHERE code = ?", (code,)).fetchone():
        raise ValidationError("رمز الجهة مستخدم مسبقاً.")
    cur = db.execute("INSERT INTO entities (code, name) VALUES (?, ?)", (code, name))
    db.commit()
    return cur.lastrowid


def create_budget_item(db, code, name, chapter):
    code = _required(code, "الرمز")
    name = _required(name, "اسم البند")
    chapter = _required(chapter, "الباب")
    if db.execute("SELECT 1 FROM budget_items WHERE code = ?", (code,)).fetchone():
        raise ValidationError("رمز البند مستخدم مسبقاً.")
    cur = db.execute(
        "INSERT INTO budget_items (code, name, chapter) VALUES (?, ?, ?)",
        (code, name, chapter),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# الاعتمادات
# ---------------------------------------------------------------------------

_BALANCE_SQL = """
SELECT a.id, a.fiscal_year, a.original_amount, a.notes,
       a.entity_id, e.name AS entity_name, e.code AS entity_code,
       a.item_id, b.name AS item_name, b.code AS item_code, b.chapter,
       COALESCE((SELECT SUM(amount) FROM adjustments
                 WHERE appropriation_id = a.id AND kind = 'increase'), 0) AS increases,
       COALESCE((SELECT SUM(amount) FROM adjustments
                 WHERE appropriation_id = a.id AND kind = 'decrease'), 0) AS decreases,
       COALESCE((SELECT SUM(amount) FROM expenditures
                 WHERE appropriation_id = a.id AND status = 'approved'), 0) AS spent,
       COALESCE((SELECT SUM(amount) FROM expenditures
                 WHERE appropriation_id = a.id AND status = 'pending'), 0) AS reserved
FROM appropriations a
JOIN entities e ON e.id = a.entity_id
JOIN budget_items b ON b.id = a.item_id
"""


def _enrich(row):
    data = dict(row)
    current = data["original_amount"] + data["increases"] - data["decreases"]
    data["current_amount"] = current
    data["available"] = current - data["spent"] - data["reserved"]
    used = data["spent"] + data["reserved"]
    data["utilization"] = round(used * 100 / current, 1) if current else (100.0 if used else 0.0)
    data["level"] = alert_level(data["utilization"])
    return data


def alert_level(utilization):
    if utilization >= 100:
        return "exhausted"
    if utilization >= CRITICAL_THRESHOLD:
        return "critical"
    if utilization >= WARNING_THRESHOLD:
        return "warning"
    return "normal"


ALERT_LABELS = {
    "normal": "طبيعي",
    "warning": "تحذير",
    "critical": "حرج",
    "exhausted": "مستنفد",
}


def get_appropriation(db, appropriation_id):
    row = db.execute(_BALANCE_SQL + " WHERE a.id = ?", (appropriation_id,)).fetchone()
    return _enrich(row) if row else None


def list_appropriations(db, fiscal_year=None, entity_id=None, item_id=None):
    clauses, params = [], []
    if fiscal_year:
        clauses.append("a.fiscal_year = ?")
        params.append(fiscal_year)
    if entity_id:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    if item_id:
        clauses.append("a.item_id = ?")
        params.append(item_id)
    sql = _BALANCE_SQL
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY a.fiscal_year DESC, e.name, b.code"
    return [_enrich(r) for r in db.execute(sql, params).fetchall()]


def fiscal_years(db):
    rows = db.execute(
        "SELECT DISTINCT fiscal_year FROM appropriations ORDER BY fiscal_year DESC"
    ).fetchall()
    return [r["fiscal_year"] for r in rows]


def create_appropriation(db, entity_id, item_id, fiscal_year, amount, notes=""):
    try:
        entity_id, item_id, fiscal_year = int(entity_id), int(item_id), int(fiscal_year)
    except (TypeError, ValueError):
        raise ValidationError("يجب اختيار الجهة والبند والسنة المالية.")
    if not 2000 <= fiscal_year <= 2100:
        raise ValidationError("السنة المالية غير صالحة.")
    if not db.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone():
        raise ValidationError("الجهة غير موجودة.")
    if not db.execute("SELECT 1 FROM budget_items WHERE id = ?", (item_id,)).fetchone():
        raise ValidationError("البند غير موجود.")
    minor = parse_amount(amount)
    if minor <= 0:
        raise ValidationError("مبلغ الاعتماد يجب أن يكون أكبر من صفر.")
    exists = db.execute(
        "SELECT 1 FROM appropriations WHERE entity_id = ? AND item_id = ? AND fiscal_year = ?",
        (entity_id, item_id, fiscal_year),
    ).fetchone()
    if exists:
        raise ValidationError("يوجد اعتماد لهذه الجهة على هذا البند في نفس السنة المالية.")
    cur = db.execute(
        "INSERT INTO appropriations (entity_id, item_id, fiscal_year, original_amount, notes)"
        " VALUES (?, ?, ?, ?, ?)",
        (entity_id, item_id, fiscal_year, minor, (notes or "").strip()),
    )
    db.commit()
    return cur.lastrowid


def _require_appropriation(db, appropriation_id):
    appr = get_appropriation(db, appropriation_id)
    if appr is None:
        raise ValidationError("الاعتماد غير موجود.")
    return appr


def adjust_appropriation(db, appropriation_id, kind, amount, reason, adj_date=None):
    """تعزيز أو تخفيض اعتماد."""
    if kind not in ("increase", "decrease"):
        raise ValidationError("نوع التعديل غير صالح.")
    appr = _require_appropriation(db, appropriation_id)
    minor = parse_amount(amount)
    if minor <= 0:
        raise ValidationError("مبلغ التعديل يجب أن يكون أكبر من صفر.")
    reason = _required(reason, "السبب")
    if kind == "decrease" and minor > appr["available"]:
        raise ValidationError(
            f"لا يمكن التخفيض بأكثر من الرصيد المتاح ({format_amount(appr['available'])})."
        )
    db.execute(
        "INSERT INTO adjustments (appropriation_id, kind, amount, reason, adj_date)"
        " VALUES (?, ?, ?, ?, ?)",
        (appr["id"], kind, minor, reason, _parse_date(adj_date)),
    )
    db.commit()


def transfer(db, from_id, to_id, amount, reason, adj_date=None):
    """مناقلة مبلغ من اعتماد إلى آخر في نفس السنة المالية."""
    source = _require_appropriation(db, from_id)
    target = _require_appropriation(db, to_id)
    if source["id"] == target["id"]:
        raise ValidationError("لا يمكن المناقلة إلى نفس الاعتماد.")
    if source["fiscal_year"] != target["fiscal_year"]:
        raise ValidationError("المناقلة مسموحة فقط بين اعتمادات نفس السنة المالية.")
    minor = parse_amount(amount)
    if minor <= 0:
        raise ValidationError("مبلغ المناقلة يجب أن يكون أكبر من صفر.")
    reason = _required(reason, "السبب")
    if minor > source["available"]:
        raise ValidationError(
            f"مبلغ المناقلة يتجاوز الرصيد المتاح ({format_amount(source['available'])})."
        )
    group = uuid.uuid4().hex
    when = _parse_date(adj_date)
    with db:
        db.execute(
            "INSERT INTO adjustments (appropriation_id, kind, amount, reason, transfer_group, adj_date)"
            " VALUES (?, 'decrease', ?, ?, ?, ?)",
            (source["id"], minor, f"مناقلة إلى {target['item_code']} - {reason}", group, when),
        )
        db.execute(
            "INSERT INTO adjustments (appropriation_id, kind, amount, reason, transfer_group, adj_date)"
            " VALUES (?, 'increase', ?, ?, ?, ?)",
            (target["id"], minor, f"مناقلة من {source['item_code']} - {reason}", group, when),
        )


def list_adjustments(db, appropriation_id):
    return db.execute(
        "SELECT * FROM adjustments WHERE appropriation_id = ? ORDER BY adj_date DESC, id DESC",
        (appropriation_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# المصروفات
# ---------------------------------------------------------------------------

def create_expenditure(db, appropriation_id, amount, description, document_no,
                       beneficiary="", exp_date=None):
    """تسجيل مصروف جديد (قيد الاعتماد) مع حجز مبلغه من رصيد الاعتماد."""
    appr = _require_appropriation(db, appropriation_id)
    minor = parse_amount(amount)
    if minor <= 0:
        raise ValidationError("مبلغ المصروف يجب أن يكون أكبر من صفر.")
    description = _required(description, "البيان")
    document_no = _required(document_no, "رقم المستند")
    if minor > appr["available"]:
        raise ValidationError(
            f"المبلغ يتجاوز الرصيد المتاح للاعتماد ({format_amount(appr['available'])})."
        )
    cur = db.execute(
        "INSERT INTO expenditures (appropriation_id, amount, description, document_no,"
        " beneficiary, exp_date) VALUES (?, ?, ?, ?, ?, ?)",
        (appr["id"], minor, description, document_no, (beneficiary or "").strip(),
         _parse_date(exp_date)),
    )
    db.commit()
    return cur.lastrowid


def set_expenditure_status(db, expenditure_id, status):
    if status not in ("approved", "rejected"):
        raise ValidationError("الحالة غير صالحة.")
    row = db.execute("SELECT status FROM expenditures WHERE id = ?", (expenditure_id,)).fetchone()
    if row is None:
        raise ValidationError("المصروف غير موجود.")
    if row["status"] != "pending":
        raise ValidationError("لا يمكن تغيير حالة مصروف تم البت فيه.")
    # المبلغ محجوز مسبقاً عند التسجيل، لذا الاعتماد لا يمكن أن يتجاوز الرصيد.
    db.execute("UPDATE expenditures SET status = ? WHERE id = ?", (status, expenditure_id))
    db.commit()


def list_expenditures(db, appropriation_id=None, status=None, fiscal_year=None, entity_id=None):
    clauses, params = [], []
    if appropriation_id:
        clauses.append("x.appropriation_id = ?")
        params.append(appropriation_id)
    if status:
        clauses.append("x.status = ?")
        params.append(status)
    if fiscal_year:
        clauses.append("a.fiscal_year = ?")
        params.append(fiscal_year)
    if entity_id:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    sql = """
        SELECT x.*, a.fiscal_year, e.name AS entity_name, b.code AS item_code,
               b.name AS item_name
        FROM expenditures x
        JOIN appropriations a ON a.id = x.appropriation_id
        JOIN entities e ON e.id = a.entity_id
        JOIN budget_items b ON b.id = a.item_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY x.exp_date DESC, x.id DESC"
    return db.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# الملخصات والتقارير
# ---------------------------------------------------------------------------

def summarize(appropriations):
    totals = {"original": 0, "current": 0, "spent": 0, "reserved": 0, "available": 0}
    for a in appropriations:
        totals["original"] += a["original_amount"]
        totals["current"] += a["current_amount"]
        totals["spent"] += a["spent"]
        totals["reserved"] += a["reserved"]
        totals["available"] += a["available"]
    used = totals["spent"] + totals["reserved"]
    totals["utilization"] = round(used * 100 / totals["current"], 1) if totals["current"] else 0.0
    return totals


def group_by(appropriations, key, label_key):
    """تجميع الاعتمادات حسب الجهة أو الباب لتقارير الملخص."""
    groups = {}
    for a in appropriations:
        groups.setdefault(a[key], {"label": a[label_key], "rows": []})["rows"].append(a)
    result = []
    for group in groups.values():
        totals = summarize(group["rows"])
        totals["label"] = group["label"]
        totals["count"] = len(group["rows"])
        result.append(totals)
    result.sort(key=lambda g: g["utilization"], reverse=True)
    return result


def alerts(appropriations):
    """الاعتمادات التي بلغت أو تجاوزت حد التحذير، مرتبة حسب الخطورة."""
    flagged = [a for a in appropriations if a["level"] != "normal"]
    flagged.sort(key=lambda a: a["utilization"], reverse=True)
    return flagged
