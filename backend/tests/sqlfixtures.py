"""بيانات مرجعية بسيطة بـ SQL مباشر لاختبارات قاعدة البيانات (المرحلة 1)."""
import uuid
from datetime import date

from sqlalchemy import text

from app.core.db import SYSTEM_USER_ID


def make_reference(session, year=2026):
    """ينشئ جهة وبابًا وبندين وسنة مفتوحة بفترات شهرية، ويعيد المعرفات."""
    ids = {k: uuid.uuid4() for k in ("entity", "chapter", "item_a", "item_b", "fy", "line_a", "line_b")}
    session.execute(text("INSERT INTO entities (id, code, name) VALUES (:id, 'E1', 'جهة اختبار')"),
                    {"id": ids["entity"]})
    session.execute(text("INSERT INTO budget_chapters (id, code, name) VALUES (:id, '2', 'الباب الثاني')"),
                    {"id": ids["chapter"]})
    for key, code, name in (("item_a", "2/16", "تجهيزات"), ("item_b", "2/18", "الصيانة")):
        session.execute(text("INSERT INTO budget_items (id, chapter_id, code, name) VALUES (:id, :c, :code, :n)"),
                        {"id": ids[key], "c": ids["chapter"], "code": code, "n": name})
    session.execute(text("INSERT INTO fiscal_years (id, year, start_date, end_date, status) "
                         "VALUES (:id, :y, :s, :e, 'OPEN')"),
                    {"id": ids["fy"], "y": year, "s": date(year, 1, 1), "e": date(year, 12, 31)})
    periods = {}
    for m in range(1, 13):
        pid = uuid.uuid4()
        end = date(year, m + 1, 1).toordinal() - 1 if m < 12 else date(year, 12, 31).toordinal()
        session.execute(text("INSERT INTO fiscal_periods (id, fiscal_year_id, period_no, start_date, end_date) "
                             "VALUES (:id, :fy, :n, :s, :e)"),
                        {"id": pid, "fy": ids["fy"], "n": m, "s": date(year, m, 1), "e": date.fromordinal(end)})
        periods[m] = pid
    ids["periods"] = periods
    for line, item in (("line_a", "item_a"), ("line_b", "item_b")):
        session.execute(text("INSERT INTO budget_lines (id, fiscal_year_id, entity_id, item_id) "
                             "VALUES (:id, :fy, :e, :i)"),
                        {"id": ids[line], "fy": ids["fy"], "e": ids["entity"], "i": ids[item]})
    return ids


def insert_entry(session, ids, *, line="line_a", txn="BUDGET_ALLOCATION", component="ALLOCATION",
                 direction=1, amount="100.000", month=1, day=15, group=None, reversal_of=None):
    eid = uuid.uuid4()
    session.execute(text(
        "INSERT INTO ledger_entries (id, fiscal_year_id, period_id, budget_line_id, txn_type, component,"
        " direction, amount, entry_date, source_type, source_id, transfer_group_id, reversal_of_id, posted_by)"
        " VALUES (:id, :fy, :p, :l, :t, :c, :d, :a, :dt, 'test', :sid, :g, :r, :u)"),
        {"id": eid, "fy": ids["fy"], "p": ids["periods"][month], "l": ids[line], "t": txn, "c": component,
         "d": direction, "a": amount, "dt": date(2026, month, day), "sid": uuid.uuid4(), "g": group,
         "r": reversal_of, "u": SYSTEM_USER_ID})
    return eid
