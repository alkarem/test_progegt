"""كتالوج الصلاحيات والأدوار (مصدر واحد في الكود، يُزامَن مع قاعدة البيانات).

راجع docs/blueprint/06-permissions.md.
"""

# (الرمز، الوصف)
PERMISSIONS: dict[str, str] = {
    # الإدارة
    "users.view": "عرض المستخدمين",
    "users.manage": "إدارة المستخدمين والأدوار والنطاقات",
    "settings.manage": "إدارة الإعدادات",
    # البيانات المرجعية
    "catalog.view": "عرض البنود والجهات والموردين",
    "catalog.manage": "إدارة البنود والجهات ومصادر التمويل",
    "suppliers.manage": "إدارة الموردين",
    "fiscal.view": "عرض السنوات والفترات المالية",
    "fiscal.manage": "إنشاء السنوات المالية وفتحها",
    "fiscal.close_period": "إقفال الفترات وإعادة فتحها",
    "fiscal.close_year": "إقفال السنة المالية",
    # الميزانية والرقابة
    "budget.view": "عرض موقف الميزانية والقيود",
    "budget.reconcile": "تشغيل مطابقة الأرصدة",
    "budget.override_grant": "منح استثناء تجاوز الرصيد",
    # المستندات المالية: إجراءات موحدة لكل نوع
    **{f"{doc}.{action}": f"{label}: {alabel}"
       for doc, label in [
           ("budget_documents", "الاعتماد الأصلي والتعديلات"),
           ("authorizations", "التفويضات"),
           ("transfers", "المناقلات"),
           ("commitments", "الارتباطات"),
           ("expenditures", "المصروفات"),
           ("adjustments", "التسويات والقيود العكسية"),
       ]
       for action, alabel in [
           ("view", "عرض"), ("create", "إنشاء وتعديل المسودة"), ("submit", "تقديم"),
           ("review", "مراجعة مالية"), ("control", "رقابة الميزانية"),
           ("supervise", "اعتماد المشرف"), ("approve", "الاعتماد النهائي والترحيل"),
           ("cancel", "إلغاء"),
       ]},
    "authorizations.over_allocate": "التفويضات: السماح بتوزيع يتجاوز قيمة التفويض",
    # أخرى
    "workflow.inbox": "صندوق المهام",
    "workflow.manage": "إدارة مسارات الموافقة",
    "reports.view": "عرض التقارير",
    "reports.export": "تصدير التقارير",
    "documents.upload": "رفع المرفقات",
    "documents.view": "عرض المرفقات",
    "alerts.view": "عرض التنبيهات",
    "alerts.manage": "إدارة قواعد التنبيه",
    "audit.view": "عرض سجل التدقيق",
    "audit.verify": "التحقق من سلامة سجل التدقيق",
    "imports.prepare": "تحضير دفعات الاستيراد",
    "imports.commit": "تنفيذ الاستيراد",
    "ai.use": "استخدام المساعد الذكي",
    "backup.manage": "النسخ الاحتياطي والاستعادة",
}

DOC_TYPES = ["budget_documents", "authorizations", "transfers", "commitments", "expenditures", "adjustments"]


def _docs(*actions: str) -> set[str]:
    return {f"{d}.{a}" for d in DOC_TYPES for a in actions}


_READ = {"catalog.view", "fiscal.view", "budget.view", "reports.view", "reports.export", "documents.view",
         "alerts.view", "ai.use"} | _docs("view")

# الأدوار الثمانية (06-permissions §2)
ROLES: dict[str, tuple[str, set[str]]] = {
    "SYSTEM_ADMIN": ("مسؤول النظام", {
        # لا صلاحيات مالية (06-permissions §3-1)
        "users.view", "users.manage", "settings.manage", "catalog.view", "catalog.manage", "suppliers.manage",
        "fiscal.view", "fiscal.manage", "budget.view", "reports.view", "reports.export", "documents.view",
        "alerts.view", "alerts.manage", "audit.view", "workflow.manage", "imports.prepare", "imports.commit",
        "backup.manage", "ai.use", "budget.reconcile",
    } | _docs("view")),
    "DATA_ENTRY": ("مدخل بيانات", _READ | _docs("create", "submit") | {
        "workflow.inbox", "documents.upload", "suppliers.manage", "imports.prepare"}),
    "FINANCIAL_REVIEWER": ("مراجع مالي", _READ | _docs("review") | {
        "workflow.inbox", "documents.upload", "adjustments.create", "adjustments.submit"}),
    "BUDGET_CONTROLLER": ("مراقب ميزانية", _READ | _docs("control", "cancel") | {
        "workflow.inbox", "fiscal.close_period", "budget.reconcile", "alerts.manage"}),
    "SUPERVISOR": ("مشرف", _READ | _docs("supervise") | {"workflow.inbox", "audit.view"}),
    "APPROVER": ("معتمد نهائي", _READ | _docs("approve") | {
        "workflow.inbox", "audit.view", "budget.override_grant", "fiscal.close_year",
        "authorizations.over_allocate", "imports.commit"}),
    "AUDITOR": ("مدقق", _READ | {"audit.view", "audit.verify", "users.view"}),
    "REPORT_VIEWER": ("مطلع تقارير", {"catalog.view", "fiscal.view", "budget.view", "reports.view",
                                      "reports.export", "alerts.view", "ai.use"}),
}

for _code, (_name, _perms) in ROLES.items():
    _unknown = _perms - PERMISSIONS.keys()
    assert not _unknown, f"صلاحيات غير معرفة في الدور {_code}: {_unknown}"
