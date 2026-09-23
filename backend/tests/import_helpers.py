"""مسار استيراد كامل عبر الواجهات، مشترك بين اختبار الملف الحقيقي والملف الاصطناعي."""
from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from app.core.db import new_session
from app.modules.catalog.models import BudgetItem
from app.modules.ledger import engine
from app.modules.ledger.models import BudgetLine
from tests.ledger_helpers import build_world

API = "/api/v1"


def run_import(client, team, user_factory, data: bytes, decisions: dict | None = None) -> dict:
    w = build_world(year=2023, basis="AUTHORIZATION", items=())
    admin = user_factory("impadmin", "SYSTEM_ADMIN")
    prep = team["DATA_ENTRY"]
    r = client.post(f"{API}/imports", headers=prep, files={"file": ("bab2.xlsx", data)},
                    data={"fiscal_year_id": str(w.fy_id)})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    analysis = client.post(f"{API}/imports/{bid}/analyze", headers=prep).json()
    client.post(f"{API}/imports/{bid}/validate", headers=prep)
    issues = client.get(f"{API}/imports/{bid}/issues", headers=prep).json()
    # الاستيراد يتطلب قبول الاستثناءات التاريخية أولًا (D-02) ومستخدمًا آخر (فصل المهام)
    first = client.post(f"{API}/imports/{bid}/commit", headers=admin)
    client.put(f"{API}/imports/{bid}/decisions", headers=prep,
               json={"accept_historical_exceptions": True, "default_date": "2023-12-31"} | (decisions or {}))
    client.post(f"{API}/imports/{bid}/validate", headers=prep)
    preview = client.get(f"{API}/imports/{bid}/preview", headers=prep).json()
    own = client.post(f"{API}/imports/{bid}/commit", headers=prep)
    done = client.post(f"{API}/imports/{bid}/commit", headers=admin)
    assert done.status_code == 200, done.text
    positions = {}
    with new_session() as s:
        for line, item in s.execute(select(BudgetLine, BudgetItem).join(BudgetItem, BudgetItem.id == BudgetLine.item_id)
                                    .where(BudgetLine.fiscal_year_id == w.fy_id)):
            p = engine.current_position(s, line)
            positions[item.code] = {"allocation": p.allocation, "transfer_in": p.transfer_in,
                                    "transfer_out": p.transfer_out, "actual": p.actual, "available": p.available}
    return {"world": w, "batch_id": bid, "analysis": analysis, "issues": issues,
            "issue_codes": Counter(i["code"] for i in issues), "first_commit": first, "own_commit": own,
            "preview": preview, "created": done.json()["created"], "positions": positions,
            "prep": prep, "admin": admin}


def dec(x) -> Decimal:
    return Decimal(x)
