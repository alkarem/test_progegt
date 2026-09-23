"""اختبارات قاعدة البيانات للمرحلة 1: عدم القابلية للتعديل، والتدقيق، والأرصدة، والثوابت."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import begin_write
from tests.sqlfixtures import insert_entry, make_reference


def balance(session, line_id):
    return session.execute(text("SELECT * FROM budget_balances WHERE budget_line_id = :l"),
                           {"l": line_id}).mappings().one()


def test_write_without_audit_context_is_rejected(session):
    with pytest.raises(DBAPIError, match="سياق التدقيق"):
        session.execute(text("INSERT INTO entities (code, name) VALUES ('X', 'x')"))


def test_budget_line_creates_balance_row(session):
    begin_write(session)
    ids = make_reference(session)
    b = balance(session, ids["line_a"])
    assert b["allocation"] == Decimal("0") and b["version"] == 0


def test_ledger_insert_updates_balance(session):
    begin_write(session)
    ids = make_reference(session)
    insert_entry(session, ids, amount="1000.500")
    insert_entry(session, ids, txn="ACTUAL_EXPENDITURE", component="ACTUAL", amount="200.250")
    b = balance(session, ids["line_a"])
    assert b["allocation"] == Decimal("1000.500")
    assert b["actual"] == Decimal("200.250")
    assert b["version"] == 2
    session.commit()
    assert session.execute(text("SELECT count(*) FROM v_balance_reconciliation")).scalar() == 0


@pytest.mark.parametrize("stmt", [
    "UPDATE ledger_entries SET amount = 1",
    "DELETE FROM ledger_entries",
    "TRUNCATE ledger_entries",
])
def test_ledger_is_immutable(session, stmt):
    begin_write(session)
    ids = make_reference(session)
    insert_entry(session, ids)
    session.commit()
    begin_write(session)
    with pytest.raises(DBAPIError, match="غير قابلة للتعديل"):
        session.execute(text(stmt))


@pytest.mark.parametrize("stmt", ["UPDATE audit_log SET action = 'X'", "DELETE FROM audit_log",
                                  "TRUNCATE audit_log"])
def test_audit_log_is_immutable(session, stmt):
    with pytest.raises(DBAPIError, match="غير قابلة للتعديل"):
        session.execute(text(stmt))


def test_amount_must_be_positive_and_direction_valid(session):
    begin_write(session)
    ids = make_reference(session)
    session.commit()
    for kwargs in ({"amount": "0"}, {"amount": "-5"}, {"direction": 2}):
        begin_write(session)
        with pytest.raises(DBAPIError):
            insert_entry(session, ids, **kwargs)
        session.rollback()


def test_commitment_cannot_go_negative(session):
    begin_write(session)
    ids = make_reference(session)
    insert_entry(session, ids, txn="COMMITMENT", component="COMMITMENT", amount="50")
    with pytest.raises(DBAPIError, match="commitment"):
        insert_entry(session, ids, txn="COMMITMENT_LIQUIDATION", component="COMMITMENT", direction=-1,
                     amount="60")


def test_closed_period_rejects_posting(session):
    begin_write(session)
    ids = make_reference(session)
    session.execute(text("UPDATE fiscal_periods SET status = 'CLOSED' WHERE id = :p"), {"p": ids["periods"][1]})
    with pytest.raises(DBAPIError, match="مقفلة"):
        insert_entry(session, ids, month=1)


def test_entry_date_must_fall_in_period(session):
    begin_write(session)
    ids = make_reference(session)
    with pytest.raises(DBAPIError, match="خارج الفترة"):
        session.execute(text(
            "INSERT INTO ledger_entries (fiscal_year_id, period_id, budget_line_id, txn_type, component,"
            " direction, amount, entry_date, source_type, source_id, posted_by)"
            " VALUES (:fy, :p, :l, 'BUDGET_ALLOCATION', 'ALLOCATION', 1, 5, '2026-03-01', 't', :s, :u)"),
            {"fy": ids["fy"], "p": ids["periods"][1], "l": ids["line_a"], "s": uuid.uuid4(),
             "u": "00000000-0000-0000-0000-000000000001"})


def test_planning_year_rejects_posting(session):
    begin_write(session)
    ids = make_reference(session)
    session.execute(text("UPDATE fiscal_years SET status = 'PLANNING' WHERE id = :f"), {"f": ids["fy"]})
    with pytest.raises(DBAPIError, match="غير مفتوحة"):
        insert_entry(session, ids)


def test_balanced_transfer_commits(session):
    begin_write(session)
    ids = make_reference(session)
    g = uuid.uuid4()
    insert_entry(session, ids, line="line_a", txn="TRANSFER_OUT", component="TRANSFER_OUT", amount="30", group=g)
    insert_entry(session, ids, line="line_b", txn="TRANSFER_IN", component="TRANSFER_IN", amount="30", group=g)
    session.commit()
    assert balance(session, ids["line_b"])["transfer_in"] == Decimal("30")


def test_unbalanced_transfer_fails_at_commit(session):
    begin_write(session)
    ids = make_reference(session)
    g = uuid.uuid4()
    insert_entry(session, ids, line="line_a", txn="TRANSFER_OUT", component="TRANSFER_OUT", amount="30", group=g)
    insert_entry(session, ids, line="line_b", txn="TRANSFER_IN", component="TRANSFER_IN", amount="29", group=g)
    with pytest.raises(DBAPIError, match="غير متوازنة"):
        session.commit()


def test_transfer_requires_group(session):
    begin_write(session)
    ids = make_reference(session)
    with pytest.raises(DBAPIError):
        insert_entry(session, ids, txn="TRANSFER_OUT", component="TRANSFER_OUT")


def test_reversal_must_mirror_original(session):
    begin_write(session)
    ids = make_reference(session)
    orig = insert_entry(session, ids, amount="100")
    with pytest.raises(DBAPIError, match="لا يطابق"):
        insert_entry(session, ids, txn="REVERSAL", component="ALLOCATION", direction=-1, amount="90",
                     reversal_of=orig)


def test_reversal_success_and_double_reversal_blocked(session):
    begin_write(session)
    ids = make_reference(session)
    orig = insert_entry(session, ids, amount="100")
    insert_entry(session, ids, txn="REVERSAL", component="ALLOCATION", direction=-1, amount="100", reversal_of=orig)
    assert balance(session, ids["line_a"])["allocation"] == Decimal("0")
    with pytest.raises(DBAPIError):
        insert_entry(session, ids, txn="REVERSAL", component="ALLOCATION", direction=-1, amount="100",
                     reversal_of=orig)


def test_audit_trail_records_changes_with_old_and_new_values(session):
    begin_write(session, reason="تصحيح الاسم")
    ids = make_reference(session)
    session.execute(text("UPDATE budget_items SET name = 'الصيانة العامة' WHERE id = :i"), {"i": ids["item_b"]})
    session.commit()
    row = session.execute(text(
        "SELECT action, old_values->>'name' AS old, new_values->>'name' AS new, changed_fields, reason, user_id"
        " FROM audit_log WHERE table_name = 'budget_items' AND action = 'UPDATE'")).mappings().one()
    assert row["old"] == "الصيانة" and row["new"] == "الصيانة العامة"
    assert row["changed_fields"] == ["name"]
    assert row["reason"] == "تصحيح الاسم"


def test_audit_excludes_secrets(session):
    begin_write(session)
    session.execute(text("INSERT INTO users (username, full_name, password_hash) VALUES ('ali', 'علي', 'SECRET')"))
    session.commit()
    new = session.execute(text("SELECT new_values FROM audit_log WHERE table_name = 'users' "
                               "ORDER BY id DESC LIMIT 1")).scalar()
    assert "password_hash" not in new


def test_audit_chain_verifies_and_detects_tampering(session, database):
    begin_write(session)
    make_reference(session)
    session.commit()
    broken, checked = session.execute(text("SELECT * FROM audit_verify_chain()")).one()
    assert broken is None and checked > 10
    # محاكاة عبث من مستخدم قاعدة بيانات يملك صلاحية تعطيل triggers
    session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_immutable"))
    session.execute(text("UPDATE audit_log SET new_values = '{\"name\": \"x\"}' WHERE id = 5"))
    session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_immutable"))
    session.commit()
    broken, _ = session.execute(text("SELECT * FROM audit_verify_chain()")).one()
    assert broken == 5
