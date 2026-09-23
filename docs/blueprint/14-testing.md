# P — استراتيجية الاختبار (Test Plan)

## 1. الأدوات

| الطبقة | الأداة |
|---|---|
| Backend | pytest، وpytest-asyncio، وhttpx (API)، و**Testcontainers PostgreSQL 16** (قاعدة حقيقية وليست SQLite)، وHypothesis (اختبارات الخصائص)، وfactory-boy |
| Frontend | Vitest، وReact Testing Library، وMSW |
| E2E | Playwright (Chromium) على نسخة Docker Compose كاملة |
| أمن | bandit، وpip-audit، وnpm audit، وOWASP ZAP baseline |
| أداء | Locust، مع بيانات مولدة (مليون قيد) |
| معمارية | import-linter (لا وحدة تكتب في ledger إلا ledger) |
| تغطية | coverage.py، والحد الأدنى 85% عمومًا و**100% فروع** لـ `modules/ledger` و`modules/workflow` |

## 2. فئات الاختبارات

| الفئة | أمثلة إلزامية |
|---|---|
| **Unit** | دوال المال (تقريب، تحويل، تنسيق)، وتطبيع العربية، ومصنف صفوف الاستيراد، ومعادلات الرصيد |
| **Financial Calculation** | سيناريو البند 41 بالكامل (§3)، وسيناريو PR ← PO ← صرف جزئي ← صرف بزيادة، والتخفيض فوق المتاح (مرفوض)، والمناقلة فوق المتاح (مرفوضة)، والإقفال والترحيل، و**Property-based**: لأي تسلسل عشوائي صالح من العمليات: INV-01 إلى INV-09 صحيحة دائمًا، والمتاح لا يصبح سالبًا دون منحة |
| **Database** | منع UPDATE/DELETE على ledger وaudit، والقيود (CHECK وUNIQUE وFK)، وtrigger توازن المناقلة، وtrigger مجموع التوزيعات، وrole permissions (app لا يستطيع DELETE)، وترحيلات Alembic صعودًا ونزولًا على قاعدة فارغة وأخرى ببيانات |
| **Concurrency** | 50 طلب صرف متوازٍ على بند متاحه 10,000 بمبلغ 1,000 لكل طلب: يُرحَّل 10 بالضبط ويُرفض 40، ومناقلتان متعاكستان متزامنتان بلا جمود (Deadlock) |
| **Workflow** | كل انتقال مسموح وممنوع، وفصل المهام (المنشئ لا يعتمد)، وعدم تخطي المراحل، وشرائح المبالغ، والإرجاع ثم التعديل ثم إعادة التقديم، والترحيل الذري (فشل في منتصف الترحيل = لا أثر) |
| **Permission** | مصفوفة آلية: كل endpoint × كل دور ← الحالة المتوقعة (200/403)، والنطاق (مستخدم مقيد ببند لا يرى غيره في القوائم والبحث والتقارير والمساعد الذكي) |
| **API** | عقود OpenAPI (Schemathesis)، وأخطاء RFC 7807، والترقيم، وIdempotency-Key، وIf-Match |
| **Integration** | الترحيل ← التنبيهات ← الإشعارات، والمرفق يُقفل عند الترحيل، والتقرير يطابق الموقف |
| **Import** | الملف الحقيقي `الباب الثانى.xlsx` (§10-6)، وملفات مشوهة (أوراق ناقصة، وأعمدة مبدلة، ونصوص في أعمدة المبالغ، وملف مكرر، وملف بماكرو) |
| **Security** | حقن SQL في كل حقل نصي وفلتر، وXSS مخزن في البيان، ورفع ملف تنفيذي بامتداد pdf، وإعادة استخدام refresh token (إلغاء العائلة)، وقفل الحساب، وJWT منتهي أو مزور أو بخوارزمية `none`، والوصول المباشر لمرفق بلا صلاحية، والحد من المعدل |
| **Reports** | كل تقرير: مجاميعه = مجموع القيود، وPDF يحتوي نصًا عربيًا صحيح الاتجاه (استخراج نص + لقطة مرجعية)، وExcel بأرقام وليس نصوصًا |
| **AI** | الأدوات تحترم النطاق، وفحص الإسناد (Grounding) يرفض رقمًا غير موجود في نتائج الأدوات، والمساعد لا يملك أي أداة كتابة (اختبار ثابت على الكتالوج) |
| **E2E** | **معيار النجاح (البند 49):** تفويض ← توزيع ← مناقلة ← ارتباط ← صرف ← تسوية ← الرصيد ← التقرير، بخمسة مستخدمين بأدوار مختلفة عبر المتصفح |
| **Regression** | كل خطأ مُصلح يضاف له اختبار يحمل رقمه، وسيناريوهات D-xx بعد اعتمادها |
| **Backup/Restore** | نسخ ثم تغيير ثم استعادة ثم مطابقة الأرصدة وسلسلة التدقيق، والتحقق من نسخة تالفة (يُكتشف) |
| **Performance** | موقف الباب أقل من 1s وقائمة القيود المرقمة أقل من 500ms (p95) عند مليون قيد و50 مستخدمًا متزامنًا |

## 3. الاختبار المالي الإلزامي (البند 41)، بصيغة قابلة للتنفيذ

```python
def test_mandatory_budget_scenario(ledger, line):
    ledger.post_original_budget(line, D("100000"))
    ledger.post_actual(line, D("20000"))
    assert position(line).available == D("80000")

    other = make_line()
    ledger.post_transfer(src=line, dst=other, amount=D("10000"))
    assert position(line).available == D("70000")

    ledger.post_transfer(src=other_funded_line(D("30000")), dst=line, amount=D("30000"))
    assert position(line).available == D("100000")

    c = ledger.post_commitment(line, D("20000"))
    assert position(line).available == D("80000")

    ledger.post_actual(line, D("15000"), commitment=c)
    p = position(line)
    assert outstanding(c) == D("5000")
    assert p.commitment == D("5000")
    assert p.actual == D("35000")
    assert p.book_balance == D("85000")
    assert p.available == D("80000")          # الارتباط يُسيَّل ولا يُخصم مرتين

    with pytest.raises(InsufficientBudget) as e:
        ledger.post_actual(line, D("80000.001"))
    assert e.value.shortfall == D("0.001")
```

## 4. بوابات الجودة في CI (لكل Pull Request)

lint (ruff، eslint) ← أنواع (mypy strict، tsc) ← unit ← integration وDB (Testcontainers) ← API والصلاحيات ← import ← frontend ← build الصور ← E2E مختصر. **لا دمج إذا فشلت أي بوابة أو انخفضت التغطية.**
