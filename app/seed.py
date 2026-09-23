"""بيانات تجريبية لعرض إمكانيات النظام."""
from . import services


def seed_demo(db, year=2026):
    entities = {
        code: services.create_entity(db, code, name)
        for code, name in [
            ("MOH", "وزارة الصحة"),
            ("MOE", "وزارة التعليم"),
            ("MOT", "وزارة النقل"),
        ]
    }
    items = {
        code: services.create_budget_item(db, code, name, chapter)
        for code, name, chapter in [
            ("1-100", "الرواتب والأجور", "الباب الأول: تعويضات العاملين"),
            ("2-210", "المستلزمات السلعية", "الباب الثاني: السلع والخدمات"),
            ("2-220", "الصيانة والتشغيل", "الباب الثاني: السلع والخدمات"),
            ("4-410", "المشاريع الإنشائية", "الباب الرابع: الأصول غير المالية"),
        ]
    }

    plan = [
        ("MOH", "1-100", "5000000", [("1200000", "رواتب الربع الأول", "approved"),
                                     ("1250000", "رواتب الربع الثاني", "approved"),
                                     ("1250000", "رواتب الربع الثالث", "pending")]),
        ("MOH", "2-210", "800000", [("420000", "أدوية ومستلزمات طبية", "approved"),
                                    ("290000", "مستلزمات مختبرات", "approved")]),
        ("MOE", "1-100", "7000000", [("1700000", "رواتب الربع الأول", "approved"),
                                     ("1700000", "رواتب الربع الثاني", "approved")]),
        ("MOE", "2-220", "600000", [("150000", "صيانة مدارس", "approved"),
                                    ("80000", "عقد نظافة", "rejected")]),
        ("MOT", "4-410", "12000000", [("6500000", "مشروع طريق سريع - دفعة 1", "approved"),
                                      ("5300000", "مشروع طريق سريع - دفعة 2", "approved")]),
        ("MOT", "2-220", "900000", [("200000", "صيانة جسور", "pending")]),
    ]

    doc = 1000
    for entity, item, amount, expenses in plan:
        appr_id = services.create_appropriation(db, entities[entity], items[item], year, amount)
        for exp_amount, desc, status in expenses:
            doc += 1
            exp_id = services.create_expenditure(
                db, appr_id, exp_amount, desc, f"DOC-{doc}", "مورد تجريبي"
            )
            if status != "pending":
                services.set_expenditure_status(db, exp_id, status)
