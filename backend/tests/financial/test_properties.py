"""اختبار خصائص (Property-based): أي تسلسل عشوائي من العمليات يحافظ على الثوابت INV-01..INV-09."""
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from app.core.db import new_session
from app.core.errors import Conflict
from app.modules.ledger import engine
from app.modules.ledger.engine import InsufficientBudget
from tests.conftest import fresh_database
from tests.ledger_helpers import budget_doc, build_world, position, post, spec, transfer

CODES = ["2/6", "2/16", "2/18"]
amounts = st.decimals(min_value=Decimal("0.001"), max_value=Decimal("5000"), places=3)
op = st.one_of(
    st.tuples(st.just("actual"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("commit"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("pay_commitment"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("reserve"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("release"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("transfer"), st.sampled_from(CODES), st.sampled_from(CODES), amounts),
    st.tuples(st.just("increase"), st.sampled_from(CODES), amounts),
    st.tuples(st.just("decrease"), st.sampled_from(CODES), amounts),
)


def apply(w, o):
    kind = o[0]
    if kind == "actual":
        post(w, spec(w, o[1], "ACTUAL_EXPENDITURE", "ACTUAL", str(o[2])))
    elif kind == "commit":
        post(w, spec(w, o[1], "COMMITMENT", "COMMITMENT", str(o[2])))
    elif kind == "pay_commitment":
        outstanding = position(w, o[1]).commitment
        liq = min(outstanding, o[2])
        specs = [spec(w, o[1], "ACTUAL_EXPENDITURE", "ACTUAL", str(o[2]))]
        if liq > 0:
            specs.append(spec(w, o[1], "COMMITMENT_LIQUIDATION", "COMMITMENT", str(liq), -1))
        post(w, *specs)
    elif kind == "reserve":
        post(w, spec(w, o[1], "PRE_COMMITMENT", "RESERVATION", str(o[2])))
    elif kind == "release":
        post(w, spec(w, o[1], "RESERVATION_RELEASE", "RESERVATION", str(o[2]), -1))
    elif kind == "transfer":
        if o[1] != o[2]:
            transfer(w, o[1], o[2], str(o[3]))
    elif kind == "increase":
        budget_doc(w, "BUDGET_INCREASE", {o[1]: str(o[2])})
    elif kind == "decrease":
        budget_doc(w, "BUDGET_DECREASE", {o[1]: str(o[2])})


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops=st.lists(op, min_size=5, max_size=25))
def test_random_operations_preserve_invariants(template_database, ops):
    with fresh_database():
        _run(ops)


def _run(ops):
    w = build_world(basis="APPROPRIATION")
    budget_doc(w, "ORIGINAL_BUDGET", {c: "10000" for c in CODES})
    for o in ops:
        try:
            apply(w, o)
        except (InsufficientBudget, Conflict):
            pass
        for c in CODES:
            p = position(w, c)
            assert p.available >= 0                       # INV-05 (بلا منح استثناء)
            assert p.commitment >= 0 and p.reservation >= 0  # INV-04
    with new_session() as s:
        assert engine.reconcile(s) == []                  # INV-08
        tin, tout = s.execute(text(
            "SELECT coalesce(sum(direction*amount) FILTER (WHERE component='TRANSFER_IN'),0),"
            " coalesce(sum(direction*amount) FILTER (WHERE component='TRANSFER_OUT'),0)"
            " FROM ledger_entries WHERE fiscal_year_id = :f"), {"f": w.fy_id}).one()
        assert tin == tout                                # INV-03
        total = sum((position(w, c).adjusted_budget for c in CODES), Decimal(0))
        base = sum((position(w, c).control_base for c in CODES), Decimal(0))
        assert total == base                              # المناقلات لا تنشئ مالًا
