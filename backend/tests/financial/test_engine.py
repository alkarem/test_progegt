"""اختبارات المحرك المالي (المرحلة 4)، ومنها الاختبار الإلزامي للبند 41."""
import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SYSTEM_USER_ID, new_session
from app.core.errors import Conflict
from app.modules.ledger import engine
from app.modules.ledger.engine import InsufficientBudget
from app.modules.ledger.models import LedgerEntry, OverrideGrant
from tests.ledger_helpers import D, actual, budget_doc, build_world, position, post, spec, transfer, write


def test_mandatory_scenario_item_41(database):
    """03-financial-model §8 و14-testing §3: كل خطوة بقيمها المطلوبة."""
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000", "2/6": "50000"})
    assert position(w, "2/18").available == D("100000")

    actual(w, "2/18", "20000")                                   # Expense = 20,000
    assert position(w, "2/18").available == D("80000")          # Balance = 80,000

    transfer(w, "2/18", "2/16", "10000")                         # Transfer Out = 10,000
    assert position(w, "2/18").available == D("70000")          # Balance = 70,000

    transfer(w, "2/6", "2/18", "30000")                          # Transfer In = 30,000
    assert position(w, "2/18").available == D("100000")         # Balance = 100,000

    post(w, spec(w, "2/18", "COMMITMENT", "COMMITMENT", "20000"))  # Commitment = 20,000
    assert position(w, "2/18").available == D("80000")          # Available = 80,000

    actual(w, "2/18", "15000", commitment_liquidation="15000")   # Actual = 15,000 على الارتباط
    p = position(w, "2/18")
    assert p.commitment == D("5000")                             # Remaining Commitment = 5,000
    assert p.actual == D("35000")
    assert p.adjusted_budget == D("120000")
    assert p.book_balance == D("85000")
    assert p.available == D("80000")                            # الارتباط يُسيَّل ولا يُخصم مرتين

    actual(w, "2/18", "6000")
    assert position(w, "2/18").available == D("74000")
    post(w, spec(w, "2/18", "CANCELLATION", "COMMITMENT", "5000", -1))
    assert position(w, "2/18").available == D("79000")

    with pytest.raises(InsufficientBudget) as e:
        actual(w, "2/18", "80000")
    assert e.value.message == "لا يوجد اعتماد متاح كافٍ لهذه العملية."
    assert e.value.details["available"] == "79,000.000"
    assert e.value.details["requested"] == "80,000.000"
    assert e.value.details["shortfall"] == "1,000.000"
    with pytest.raises(InsufficientBudget) as e:
        actual(w, "2/18", "79000.001")
    assert e.value.details["shortfall"] == "0.001"
    actual(w, "2/18", "79000")
    assert position(w, "2/18").available == D("0")
    with new_session() as s:
        assert engine.reconcile(s) == []


def test_blueprint_example_45000(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000", "2/6": "20000", "2/16": "5000"})
    transfer(w, "2/6", "2/18", "20000")
    transfer(w, "2/18", "2/16", "5000")
    actual(w, "2/18", "60000")
    post(w, spec(w, "2/18", "COMMITMENT", "COMMITMENT", "10000"))
    p = position(w, "2/18")
    assert (p.adjusted_budget, p.book_balance, p.available) == (D("115000"), D("55000"), D("45000"))


def test_purchase_request_to_order_to_payments(database):
    """03-financial-model §6.3"""
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    post(w, spec(w, "2/18", "PRE_COMMITMENT", "RESERVATION", "30000"))
    assert position(w, "2/18").available == D("70000")
    post(w, spec(w, "2/18", "RESERVATION_RELEASE", "RESERVATION", "30000", -1),
         spec(w, "2/18", "COMMITMENT", "COMMITMENT", "28000"))
    assert position(w, "2/18").available == D("72000")
    actual(w, "2/18", "10000", commitment_liquidation="10000")
    assert position(w, "2/18").available == D("72000")
    actual(w, "2/18", "18500", commitment_liquidation="18000")
    p = position(w, "2/18")
    assert p.available == D("71500") and p.commitment == D("0")


def test_reservations_not_counted_when_disabled(database):
    w = build_world(basis="APPROPRIATION", count_reservations=False)   # D-12 قابل للتعطيل
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    post(w, spec(w, "2/18", "PRE_COMMITMENT", "RESERVATION", "400"))
    assert position(w, "2/18").available == D("1000")


def test_two_level_control(database):
    """D-01: الاعتماد سقف للتفويض، والتفويض سقف للصرف."""
    w = build_world(basis="TWO_LEVEL")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "100000"})
    assert position(w, "2/18").available == D("0")                       # لا صرف قبل التفويض
    with pytest.raises(InsufficientBudget):
        actual(w, "2/18", "1")
    post(w, spec(w, "2/18", "BUDGET_ALLOCATION", "ALLOCATION", "60000"))
    p = position(w, "2/18")
    assert (p.available, p.unallocated) == (D("60000"), D("40000"))
    with pytest.raises(Conflict) as e:
        post(w, spec(w, "2/18", "BUDGET_ALLOCATION", "ALLOCATION", "40000.001"))
    assert e.value.code == "EXCEEDS_APPROPRIATION"
    # تخفيض الاعتماد تحت المفوَّض مرفوض
    with pytest.raises(Conflict):
        budget_doc(w, "BUDGET_DECREASE", {"2/18": "40001"})
    budget_doc(w, "BUDGET_DECREASE", {"2/18": "40000"})
    assert position(w, "2/18").unallocated == D("0")


def test_authorization_basis_increase_goes_to_allocation(database):
    w = build_world(basis="AUTHORIZATION")
    budget_doc(w, "BUDGET_INCREASE", {"2/18": "500"})
    p = position(w, "2/18")
    assert (p.allocation, p.appropriation, p.available) == (D("500"), D("0"), D("500"))


def test_decrease_beyond_available_rejected(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    actual(w, "2/18", "700")
    with pytest.raises(InsufficientBudget) as e:
        budget_doc(w, "BUDGET_DECREASE", {"2/18": "301"})
    assert e.value.details["shortfall"] == "1.000"


def test_original_budget_only_once_per_line(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    with pytest.raises(Conflict) as e:
        budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "5"})
    assert e.value.code == "ORIGINAL_ALREADY_POSTED"


def test_transfer_exceeding_available_rejected_atomically(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "100"})
    with pytest.raises(InsufficientBudget):
        transfer(w, "2/6", "2/16", "100.001")
    assert position(w, "2/16").transfer_in == D("0")   # لا أثر جزئي


def test_reversal_of_consumed_transfer_is_blocked(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "1000"})
    res = transfer(w, "2/6", "2/16", "600")
    src = res.entries[0].source_id
    actual(w, "2/16", "500")
    with new_session() as s:
        write(s)
        specs = engine.reversal_specs(s, "transfer", src, "خطأ")
        with pytest.raises(InsufficientBudget):
            engine.post(s, engine.PostingRequest(w.fy_id, date(2026, 4, 1), "reversal", uuid.uuid4(),
                                                 SYSTEM_USER_ID, specs))


def test_reversal_of_unconsumed_transfer_and_no_double_reversal(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/6": "1000"})
    src = transfer(w, "2/6", "2/16", "600").entries[0].source_id
    with new_session() as s:
        write(s)
        specs = engine.reversal_specs(s, "transfer", src, "خطأ في البند")
        assert {x.transfer_group_id for x in specs} != {None} and len({x.transfer_group_id for x in specs}) == 1
        engine.post(s, engine.PostingRequest(w.fy_id, date(2026, 4, 1), "reversal", uuid.uuid4(),
                                             SYSTEM_USER_ID, specs))
        s.commit()
    assert position(w, "2/6").available == D("1000") and position(w, "2/16").available == D("0")
    with new_session() as s, pytest.raises(Conflict) as e:
        engine.reversal_specs(s, "transfer", src, "مرة ثانية")
    assert e.value.code == "NOTHING_TO_REVERSE"


def test_override_grant_covers_shortfall_within_limit(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    with new_session() as s:
        write(s)
        grantor = s.execute(text("INSERT INTO users (username, full_name, password_hash) VALUES "
                                 "('grantor', 'مانح', 'x') RETURNING id")).scalar()
        g = OverrideGrant(user_id=SYSTEM_USER_ID, budget_line_id=w.lines["2/18"], max_amount=D("300"),
                          valid_from=datetime.now(UTC) - timedelta(minutes=1),
                          valid_to=datetime.now(UTC) + timedelta(hours=1), reason="حالة طارئة", granted_by=grantor)
        s.add(g)
        s.commit()
        gid = g.id
    res = post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "1200"), override_grant_id=gid)
    assert res.override_used == D("200")
    assert all(e.override_grant_id == gid for e in res.entries)
    assert position(w, "2/18").available == D("-200")
    with pytest.raises(InsufficientBudget) as e:
        post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "101"), override_grant_id=gid)
    assert e.value.code == "OVERRIDE_EXCEEDED"
    with new_session() as s:
        assert s.get(OverrideGrant, gid).used_amount == D("200")


def test_override_self_grant_forbidden_by_database(database):
    w = build_world(basis="APPROPRIATION")
    with new_session() as s:
        write(s)
        s.add(OverrideGrant(user_id=SYSTEM_USER_ID, budget_line_id=w.lines["2/18"], max_amount=D("1"),
                            valid_from=datetime.now(UTC), valid_to=datetime.now(UTC) + timedelta(hours=1),
                            reason="x", granted_by=SYSTEM_USER_ID))
        with pytest.raises(DBAPIError):
            s.flush()


def test_historical_exception_allows_negative_and_is_flagged(database):
    w = build_world(year=2023, basis="AUTHORIZATION")
    res = post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "25000"), historical_exception=True,
               date_is_estimated=True, on=date(2023, 12, 31))
    assert res.entries[0].is_historical_exception and res.entries[0].date_is_estimated
    assert position(w, "2/18").available == D("-25000")
    with pytest.raises(InsufficientBudget):  # الصرف الجديد ما زال ممنوعًا
        actual(w, "2/18", "1")


def test_closed_period_and_unopened_year(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"})
    with new_session() as s:
        write(s)
        s.execute(text("UPDATE fiscal_periods SET status='CLOSED' WHERE fiscal_year_id=:f AND period_no=3"),
                  {"f": w.fy_id})
        s.commit()
    with pytest.raises(Conflict) as e:
        actual(w, "2/18", "1")  # 1 مارس
    assert e.value.code == "PERIOD_CLOSED"
    with pytest.raises(Exception) as e:
        post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "1"), on=date(2027, 1, 1))
    assert getattr(e.value, "code", "") == "DATE_OUTSIDE_YEAR"


def test_position_as_of_uses_ledger_dates(database):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "1000"}, on=date(2026, 1, 5))
    post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "100"), on=date(2026, 2, 10))
    post(w, spec(w, "2/18", "ACTUAL_EXPENDITURE", "ACTUAL", "200"), on=date(2026, 5, 10))
    with new_session() as s:
        line = engine.get_line(s, w.lines["2/18"])
        assert engine.position_as_of(s, line, date(2026, 3, 1)).available == D("900")
        assert engine.position_as_of(s, line, None) == engine.current_position(s, line)


def test_concurrent_postings_never_overspend(database):
    """NFR-02: 30 طلب صرف متوازٍ بقيمة 1,000 على رصيد 10,000 ← ينجح 10 بالضبط."""
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {"2/18": "10000"})
    results = []
    lock = threading.Lock()

    def worker():
        try:
            actual(w, "2/18", "1000")
            ok = True
        except InsufficientBudget:
            ok = False
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10 and results.count(False) == 20
    assert position(w, "2/18").available == D("0")
    with new_session() as s:
        assert s.scalar(select(text("count(*)")).select_from(LedgerEntry).where(
            LedgerEntry.component == "ACTUAL")) == 10
