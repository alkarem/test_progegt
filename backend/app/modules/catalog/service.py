"""خدمات البيانات المرجعية: الأبواب والبنود والجهات ومصادر التمويل والموردون."""
import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.catalog.models import BudgetChapter, BudgetItem, Entity, FundingSource, Supplier
from app.modules.ledger.models import BudgetLine, LedgerEntry
from app.shared.arabic import normalize_party_name

# معايرة على أزواج حقيقية من ملف Excel: «وابناءه/واباءه» 0.80، «الاتقان/الاتفان…» 0.76، «التاج…العامه/التاج» 0.81
SIMILARITY_THRESHOLD = 0.7


def get_chapter(session: Session, chapter_id: uuid.UUID) -> BudgetChapter:
    c = session.get(BudgetChapter, chapter_id)
    if c is None:
        raise NotFound("الباب غير موجود.")
    return c


def get_item(session: Session, item_id: uuid.UUID) -> BudgetItem:
    i = session.get(BudgetItem, item_id)
    if i is None:
        raise NotFound("البند غير موجود.")
    return i


def create_chapter(session: Session, code: str, name: str, display_order: int = 0) -> BudgetChapter:
    if session.scalar(select(BudgetChapter.id).where(BudgetChapter.code == code)):
        raise Conflict("رمز الباب مستخدم مسبقًا.", code="DUPLICATE_CODE")
    c = BudgetChapter(code=code, name=name, display_order=display_order)
    session.add(c)
    session.flush()
    return c


def _item_has_entries(session: Session, item_id: uuid.UUID) -> bool:
    return bool(session.scalar(select(exists().where(LedgerEntry.budget_line_id == BudgetLine.id,
                                                     BudgetLine.item_id == item_id))))


def _item_has_lines(session: Session, item_id: uuid.UUID) -> bool:
    return bool(session.scalar(select(exists().where(BudgetLine.item_id == item_id))))


def _validate_parent(session: Session, item: BudgetItem, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        return
    parent = get_item(session, parent_id)
    if parent.chapter_id != item.chapter_id:
        raise ValidationFailed("البند الأب يجب أن يكون في الباب نفسه.", code="PARENT_CHAPTER_MISMATCH")
    node = parent
    while node is not None:  # منع الحلقات
        if node.id == item.id:
            raise ValidationFailed("لا يمكن جعل البند أبًا لنفسه أو لأحد أسلافه.", code="ITEM_CYCLE")
        node = session.get(BudgetItem, node.parent_id) if node.parent_id else None
    if parent.is_postable:
        if _item_has_entries(session, parent.id):
            raise Conflict("البند الأب عليه حركات مرحّلة، فلا يمكن تحويله إلى بند تجميعي.",
                           code="PARENT_HAS_ENTRIES")
        parent.is_postable = False  # FR-IT-02: البند الأب يجمع أبناءه فقط


def create_item(session: Session, *, chapter_id: uuid.UUID, code: str, name: str, parent_id: uuid.UUID | None,
                budget_type: str, display_order: int) -> BudgetItem:
    chapter = get_chapter(session, chapter_id)
    code = code.replace(" ", "")
    if not code.startswith(f"{chapter.code}/"):
        raise ValidationFailed(f"رمز البند يجب أن يبدأ برمز الباب «{chapter.code}/».", code="ITEM_CODE_PREFIX")
    if session.scalar(select(BudgetItem.id).where(BudgetItem.chapter_id == chapter_id, BudgetItem.code == code)):
        raise Conflict("رمز البند مستخدم مسبقًا في هذا الباب.", code="DUPLICATE_CODE")
    item = BudgetItem(id=uuid.uuid4(), chapter_id=chapter_id, code=code, name=name.strip(),
                      budget_type=budget_type, display_order=display_order)
    _validate_parent(session, item, parent_id)
    item.parent_id = parent_id
    session.add(item)
    session.flush()
    return item


def update_item(session: Session, item: BudgetItem, changes: dict) -> BudgetItem:
    if "parent_id" in changes and changes["parent_id"] != item.parent_id:
        _validate_parent(session, item, changes["parent_id"])
        item.parent_id = changes["parent_id"]
    if "is_postable" in changes and changes["is_postable"] != item.is_postable:
        if changes["is_postable"] is False and _item_has_entries(session, item.id):
            raise Conflict("البند عليه حركات مرحّلة، فلا يمكن جعله غير قابل للترحيل.", code="ITEM_HAS_ENTRIES")
        if changes["is_postable"] and session.scalar(select(exists().where(BudgetItem.parent_id == item.id))):
            raise Conflict("البند له بنود فرعية، فلا يقبل الترحيل مباشرة.", code="ITEM_HAS_CHILDREN")
        item.is_postable = changes["is_postable"]
    for k in ("name", "budget_type", "display_order", "is_active"):
        if k in changes:
            setattr(item, k, changes[k].strip() if isinstance(changes[k], str) else changes[k])
    session.flush()
    return item


def delete_item(session: Session, item: BudgetItem) -> None:
    """الحذف مسموح فقط لبند لم يُستخدم أبدًا؛ وإلا يُعطَّل (FR-IT-03)."""
    if _item_has_lines(session, item.id) or session.scalar(select(exists().where(BudgetItem.parent_id == item.id))):
        raise Conflict("البند مستخدم أو له بنود فرعية؛ عطّله بدل حذفه.", code="ITEM_IN_USE")
    session.delete(item)
    session.flush()


def list_items(session: Session, principal: Principal, chapter_id: uuid.UUID | None, active_only: bool):
    stmt = select(BudgetItem, BudgetChapter.code.label("chapter_code")).join(
        BudgetChapter, BudgetChapter.id == BudgetItem.chapter_id)
    if chapter_id:
        stmt = stmt.where(BudgetItem.chapter_id == chapter_id)
    if active_only:
        stmt = stmt.where(BudgetItem.is_active.is_(True))
    if "ITEM" in principal.scopes:
        stmt = stmt.where(BudgetItem.id.in_(principal.scopes["ITEM"]))
    if "CHAPTER" in principal.scopes:
        stmt = stmt.where(BudgetItem.chapter_id.in_(principal.scopes["CHAPTER"]))
    return stmt.order_by(BudgetChapter.display_order, BudgetItem.display_order, BudgetItem.code)


def create_entity(session: Session, code: str, name: str, parent_id: uuid.UUID | None) -> Entity:
    if session.scalar(select(Entity.id).where(Entity.code == code)):
        raise Conflict("رمز الجهة مستخدم مسبقًا.", code="DUPLICATE_CODE")
    if parent_id and session.get(Entity, parent_id) is None:
        raise ValidationFailed("الجهة الأم غير موجودة.", code="UNKNOWN_PARENT")
    e = Entity(code=code, name=name.strip(), parent_id=parent_id)
    session.add(e)
    session.flush()
    return e


def create_funding_source(session: Session, code: str, name: str) -> FundingSource:
    if session.scalar(select(FundingSource.id).where(FundingSource.code == code)):
        raise Conflict("رمز مصدر التمويل مستخدم مسبقًا.", code="DUPLICATE_CODE")
    f = FundingSource(code=code, name=name.strip())
    session.add(f)
    session.flush()
    return f


def find_similar_suppliers(session: Session, name: str, threshold: float = 0.6) -> list[tuple[Supplier, float]]:
    norm = normalize_party_name(name)
    sim = func.similarity(Supplier.name_normalized, norm)
    rows = session.execute(select(Supplier, sim).where(sim >= threshold).order_by(sim.desc()).limit(5)).all()
    return [(r[0], float(r[1])) for r in rows]


def create_supplier(session: Session, *, name: str, kind: str, tax_no: str | None, commercial_reg: str | None,
                    phone: str | None, notes: str | None, allow_similar: bool) -> Supplier:
    norm = normalize_party_name(name)
    if not norm:
        raise ValidationFailed("اسم المورد مطلوب.", code="SUPPLIER_NAME_REQUIRED")
    if session.scalar(select(Supplier.id).where(Supplier.name_normalized == norm)):
        raise Conflict("يوجد مورد بالاسم نفسه.", code="DUPLICATE_SUPPLIER")
    if not allow_similar:
        similar = find_similar_suppliers(session, name, threshold=SIMILARITY_THRESHOLD)
        if similar:
            raise Conflict("يوجد مورد باسم مشابه جدًا؛ تأكد أنه ليس المورد نفسه.", code="SIMILAR_SUPPLIER",
                           details={"similar": [{"id": str(s.id), "name": s.name, "similarity": round(v, 2)}
                                                for s, v in similar]})
    s = Supplier(name=name.strip(), name_normalized=norm, kind=kind, tax_no=tax_no, commercial_reg=commercial_reg,
                 phone=phone, notes=notes)
    session.add(s)
    session.flush()
    return s
