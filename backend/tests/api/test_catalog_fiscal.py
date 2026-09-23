"""البنود والجهات والموردون والسنوات المالية (المرحلة 3)."""
from sqlalchemy import text

from app.core.db import new_session
from tests.conftest import seed_reference

API = "/api/v1"


def _admin(user_factory):
    return user_factory("admin", "SYSTEM_ADMIN")


def test_seed_loads_chapter_2_items_with_corrected_names(client, user_factory):
    seed_reference()
    seed_reference()  # idempotent
    h = _admin(user_factory)
    items = client.get(f"{API}/items", headers=h).json()
    assert [i["code"] for i in items] == [f"2/{n}" for n in range(1, 30)]
    names = {i["code"]: i["name"] for i in items}
    assert names["2/22"] == "أدوية وما في حكمها"          # DQ-15
    assert names["2/25"] == "الإعانات والمساعدات والمنح"   # DQ-16
    assert names["2/18"] == "الصيانة"                      # DQ-17 (الورقة: «الصياتة»)
    ents = client.get(f"{API}/entities", headers=h).json()
    assert ents[0]["name"] == "مراقبة الخدمات المالية الواحات / جالو"


def test_item_code_must_match_chapter_and_be_unique(client, user_factory):
    seed_reference()
    h = _admin(user_factory)
    ch = client.get(f"{API}/chapters", headers=h).json()[0]["id"]
    r = client.post(f"{API}/items", headers=h, json={"chapter_id": ch, "code": "3/1", "name": "بند خطأ"})
    assert r.status_code == 422 and r.json()["code"] == "ITEM_CODE_PREFIX"
    r = client.post(f"{API}/items", headers=h, json={"chapter_id": ch, "code": "2/18", "name": "مكرر"})
    assert r.status_code == 409
    r = client.post(f"{API}/items", headers=h, json={"chapter_id": ch, "code": "2 / 30", "name": "بند جديد"})
    assert r.status_code == 201 and r.json()["code"] == "2/30"


def test_child_item_makes_parent_non_postable_and_tree(client, user_factory):
    seed_reference()
    h = _admin(user_factory)
    ch = client.get(f"{API}/chapters", headers=h).json()[0]["id"]
    parent = next(i for i in client.get(f"{API}/items", headers=h).json() if i["code"] == "2/18")
    r = client.post(f"{API}/items", headers=h, json={"chapter_id": ch, "code": "2/18/1", "name": "صيانة المباني",
                                                     "parent_id": parent["id"]})
    assert r.status_code == 201
    parent = next(i for i in client.get(f"{API}/items", headers=h).json() if i["code"] == "2/18")
    assert parent["is_postable"] is False
    tree = client.get(f"{API}/items/tree", headers=h).json()
    node = next(n for n in tree if n["code"] == "2/18")
    assert [c["code"] for c in node["children"]] == ["2/18/1"]
    # منع الحلقة
    r = client.patch(f"{API}/items/{parent['id']}", headers=h, json={"parent_id": r.json()["id"]})
    assert r.status_code == 422 and r.json()["code"] == "ITEM_CYCLE"


def test_unused_item_can_be_deleted_but_used_item_must_be_deactivated(client, user_factory):
    seed_reference()
    h = _admin(user_factory)
    ch = client.get(f"{API}/chapters", headers=h).json()[0]["id"]
    new = client.post(f"{API}/items", headers=h, json={"chapter_id": ch, "code": "2/31", "name": "مؤقت"}).json()
    assert client.delete(f"{API}/items/{new['id']}", headers=h).status_code == 204
    fy = client.post(f"{API}/fiscal-years", headers=h, json={"year": 2026}).json()
    item = next(i for i in client.get(f"{API}/items", headers=h).json() if i["code"] == "2/1")
    ent = client.get(f"{API}/entities", headers=h).json()[0]
    with new_session() as s:
        s.execute(text("SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true)"))
        s.execute(text("INSERT INTO budget_lines (fiscal_year_id, entity_id, item_id) VALUES (:f, :e, :i)"),
                  {"f": fy["id"], "e": ent["id"], "i": item["id"]})
        s.commit()
    r = client.delete(f"{API}/items/{item['id']}", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "ITEM_IN_USE"
    r = client.patch(f"{API}/items/{item['id']}", headers=h, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False


def test_supplier_duplicate_and_similarity_detection(client, user_factory):
    h = user_factory("clerk", "DATA_ENTRY")
    r = client.post(f"{API}/suppliers", headers=h, json={"name": "محلات عيسي شحات وابناءه"})
    assert r.status_code == 201
    r = client.post(f"{API}/suppliers", headers=h, json={"name": "محلات عيسى شحات وابناءه"})
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_SUPPLIER"   # ى ↔ ي
    r = client.post(f"{API}/suppliers", headers=h, json={"name": "محلات عيسي شحات واباءه"})
    assert r.status_code == 409 and r.json()["code"] == "SIMILAR_SUPPLIER"
    r = client.post(f"{API}/suppliers", headers=h, json={"name": "محلات عيسي شحات واباءه", "allow_similar": True})
    assert r.status_code == 201
    r = client.post(f"{API}/suppliers", headers=h, json={"name": "السيد / صالح محمد سليم", "kind": "PERSON"})
    assert r.status_code == 201
    found = client.get(f"{API}/suppliers", headers=h, params={"q": "صالح"}).json()
    assert found["total"] == 1


def test_fiscal_year_creation_generates_13_periods(client, user_factory):
    seed_reference()
    h = _admin(user_factory)
    fy = client.post(f"{API}/fiscal-years", headers=h, json={"year": 2026}).json()
    assert fy["status"] == "PLANNING" and fy["control_basis"] == "TWO_LEVEL"
    assert fy["carry_forward_item_id"] is not None  # D-08: 2/28 افتراضيًا
    periods = client.get(f"{API}/fiscal-years/{fy['id']}/periods", headers=h).json()
    assert len(periods) == 13
    assert periods[1]["start_date"] == "2026-02-01" and periods[1]["end_date"] == "2026-02-28"
    assert periods[12]["period_no"] == 13 and periods[12]["start_date"] == "2026-12-31"
    assert client.post(f"{API}/fiscal-years", headers=h, json={"year": 2026}).status_code == 409


def test_open_year_and_control_basis_lock(client, user_factory):
    h = _admin(user_factory)
    fy = client.post(f"{API}/fiscal-years", headers=h, json={"year": 2023, "control_basis": "AUTHORIZATION"}).json()
    assert client.post(f"{API}/fiscal-years/{fy['id']}/open", headers=h).json()["status"] == "OPEN"
    r = client.patch(f"{API}/fiscal-years/{fy['id']}", headers=h, json={"control_basis": "TWO_LEVEL"})
    assert r.status_code == 409 and r.json()["code"] == "CONTROL_BASIS_LOCKED"
    assert client.post(f"{API}/fiscal-years/{fy['id']}/open", headers=h).status_code == 409


def test_period_close_and_reopen_requires_permission_and_reason(client, user_factory):
    admin = _admin(user_factory)
    ctrl = user_factory("ctrl", "BUDGET_CONTROLLER")
    clerk = user_factory("clerk", "DATA_ENTRY")
    fy = client.post(f"{API}/fiscal-years", headers=admin, json={"year": 2026}).json()
    per = client.get(f"{API}/fiscal-years/{fy['id']}/periods", headers=admin).json()[0]
    assert client.post(f"{API}/periods/{per['id']}/close", headers=clerk).status_code == 403
    assert client.post(f"{API}/periods/{per['id']}/close", headers=ctrl).json()["status"] == "CLOSED"
    assert client.post(f"{API}/periods/{per['id']}/reopen", headers=ctrl, json={}).status_code == 422
    r = client.post(f"{API}/periods/{per['id']}/reopen", headers=ctrl, json={"reason": "تسجيل مستند متأخر"})
    assert r.json()["status"] == "OPEN"
    with new_session() as s:
        reason = s.scalar(text("SELECT reason FROM audit_log WHERE table_name='fiscal_periods' "
                               "ORDER BY id DESC LIMIT 1"))
    assert reason == "تسجيل مستند متأخر"


def test_item_scope_restricts_listing(client, user_factory):
    seed_reference()
    with new_session() as s:
        item_id = s.scalar(text("SELECT id FROM budget_items WHERE code = '2/18'"))
    h = user_factory("scoped", "DATA_ENTRY", scopes={"ITEM": [item_id]})
    items = client.get(f"{API}/items", headers=h).json()
    assert [i["code"] for i in items] == ["2/18"]
