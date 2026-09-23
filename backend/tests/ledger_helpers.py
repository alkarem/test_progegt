"""أدوات اختبار محرك الحركات على مستوى الخدمة."""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.db import SYSTEM_USER_ID, AuditContext, new_session, set_audit_context
from app.core.deps import Principal
from app.core.permissions import PERMISSIONS
from app.modules.budget import service as budget
from app.modules.catalog.models import BudgetItem, Entity
from app.modules.catalog.seed import seed_reference_data
from app.modules.fiscal import service as fiscal
from app.modules.ledger import engine
from app.modules.ledger.engine import EntrySpec, PostingRequest

D = Decimal
SYSTEM = Principal(user_id=SYSTEM_USER_ID, username="system", full_name="system", session_id=uuid.uuid4(),
                   permissions=frozenset(PERMISSIONS))


@dataclass
class World:
    fy_id: uuid.UUID
    entity_id: uuid.UUID
    lines: dict[str, uuid.UUID]  # رمز البند ← سطر الميزانية
    year: int


def write(s, reason="test"):
    set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason=reason))


def build_world(year=2026, basis="APPROPRIATION", items=("2/6", "2/16", "2/18"), count_reservations=True) -> World:
    with new_session() as s:
        write(s)
        seed_reference_data(s)
        fy = fiscal.create_year(s, year=year, start_date=None, end_date=None, control_basis=basis,
                                count_reservations=count_reservations, carry_forward_item_id=None)
        fiscal.open_year(s, fy)
        ent = s.scalar(select(Entity))
        lines = {}
        for code in items:
            item = s.scalar(select(BudgetItem).where(BudgetItem.code == code))
            lines[code] = engine.get_or_create_line(s, fy.id, ent.id, item.id).id
        s.commit()
        return World(fy.id, ent.id, lines, year)


def item_id(s, code):
    return s.scalar(select(BudgetItem.id).where(BudgetItem.code == code))


def budget_doc(w: World, kind: str, amounts: dict[str, str], on=None):
    """ينشئ مستند ميزانية ويرحّله مباشرة (دورة الموافقات تُختبر منفصلة)."""
    with new_session() as s:
        write(s)
        doc = budget.create_draft(s, SYSTEM, fiscal_year_id=w.fy_id, kind=kind, doc_date=on or date(w.year, 1, 2),
                                  description="اختبار", reference=None,
                                  lines=[{"entity_id": w.entity_id, "item_id": item_id(s, c), "amount": D(a)}
                                         for c, a in amounts.items()])
        budget.post(s, doc, SYSTEM_USER_ID)
        s.commit()
        return doc.id


def post(w: World, *specs: EntrySpec, on=None, source_type="test", source_id=None, **kw):
    with new_session() as s:
        write(s)
        res = engine.post(s, PostingRequest(fiscal_year_id=w.fy_id, entry_date=on or date(w.year, 3, 1),
                                            source_type=source_type, source_id=source_id or uuid.uuid4(),
                                            posted_by=SYSTEM_USER_ID, entries=list(specs), **kw))
        s.commit()
        return res


def spec(w: World, code: str, txn: str, comp: str, amount: str, direction=1, **kw) -> EntrySpec:
    return EntrySpec(budget_line_id=w.lines[code], txn_type=txn, component=comp, direction=direction,
                     amount=D(amount), **kw)


def actual(w, code, amount, commitment_liquidation: str | None = None):
    specs = [spec(w, code, "ACTUAL_EXPENDITURE", "ACTUAL", amount)]
    if commitment_liquidation:
        specs.append(spec(w, code, "COMMITMENT_LIQUIDATION", "COMMITMENT", commitment_liquidation, -1))
    return post(w, *specs)


def transfer(w, src, dst, amount):
    g = uuid.uuid4()
    return post(w, spec(w, src, "TRANSFER_OUT", "TRANSFER_OUT", amount, transfer_group_id=g),
                spec(w, dst, "TRANSFER_IN", "TRANSFER_IN", amount, transfer_group_id=g), source_type="transfer")


def position(w, code) -> engine.Position:
    with new_session() as s:
        return engine.current_position(s, engine.get_line(s, w.lines[code]))
