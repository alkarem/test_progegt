"""البيانات المرجعية للباب الثاني بأسماء مصححة من تحليل ملف Excel (00-excel-analysis §1، DQ-15/16/17)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.models import BudgetChapter, BudgetItem, Entity, FundingSource

CHAPTER_2 = ("2", "الباب الثاني: النفقات التسييرية")

CHAPTER_2_ITEMS = [
    ("2/1", "أتعاب ومكافآت لغير العاملين"),
    ("2/2", "وقود وزيوت وقوى محركة"),
    ("2/3", "الكهرباء"),
    ("2/4", "البريد"),
    ("2/5", "المياه"),
    ("2/6", "مطبوعات وقرطاسية وأدوات مكتبية ولوازم التصوير"),
    ("2/7", "نفقات السفر والمبيت والمهمات الرسمية"),
    ("2/8", "إعلان وعلاقات عامة وضيافة"),
    ("2/9", "اشتراكات ومساهمات وحصص دولية ومحلية"),
    ("2/10", "مصروفات النظافة"),
    ("2/11", "إيجارات المباني ومصروفات النقل والشحن والتأمين"),
    ("2/12", "التأمينات والضرائب والرسوم"),
    ("2/13", "نفقات انعقاد المؤتمرات"),
    ("2/14", "معدات طبية"),
    ("2/15", "كتب ومراجع"),
    ("2/16", "تجهيزات (أغطية ومفروشات وغيرها)"),
    ("2/17", "قطع غيار ومهمات وأدوات"),
    ("2/18", "الصيانة"),
    ("2/19", "تدريب وبعثات"),
    ("2/20", "مصروفات النشاط المدرسي"),
    ("2/21", "شراء مواد وخامات"),
    ("2/22", "أدوية وما في حكمها"),
    ("2/23", "أغذية لغير العاملين"),
    ("2/24", "التزامات قانونية"),
    ("2/25", "الإعانات والمساعدات والمنح"),
    ("2/26", "مصروفات العلاج"),
    ("2/27", "مصروفات خدمية"),
    ("2/28", "مصروفات سنوات سابقة"),
    ("2/29", "دعم ميزانيات جهات أخرى"),
]

DEFAULT_ENTITY = ("WAHAT-JALO", "مراقبة الخدمات المالية الواحات / جالو")
DEFAULT_FUNDING = ("TREASURY", "الخزانة العامة")


def seed_reference_data(session: Session) -> dict[str, int]:
    """إدراج غير مكرر (idempotent): يضيف الناقص فقط ولا يغيّر الموجود."""
    created = {"chapters": 0, "items": 0, "entities": 0, "funding_sources": 0}
    chapter = session.scalar(select(BudgetChapter).where(BudgetChapter.code == CHAPTER_2[0]))
    if chapter is None:
        chapter = BudgetChapter(code=CHAPTER_2[0], name=CHAPTER_2[1], display_order=2)
        session.add(chapter)
        session.flush()
        created["chapters"] += 1
    existing = set(session.scalars(select(BudgetItem.code).where(BudgetItem.chapter_id == chapter.id)))
    for order, (code, name) in enumerate(CHAPTER_2_ITEMS, start=1):
        if code not in existing:
            session.add(BudgetItem(chapter_id=chapter.id, code=code, name=name, display_order=order))
            created["items"] += 1
    if session.scalar(select(Entity).where(Entity.code == DEFAULT_ENTITY[0])) is None:
        session.add(Entity(code=DEFAULT_ENTITY[0], name=DEFAULT_ENTITY[1]))
        created["entities"] += 1
    if session.scalar(select(FundingSource).where(FundingSource.code == DEFAULT_FUNDING[0])) is None:
        session.add(FundingSource(code=DEFAULT_FUNDING[0], name=DEFAULT_FUNDING[1]))
        created["funding_sources"] += 1
    session.flush()
    return created
