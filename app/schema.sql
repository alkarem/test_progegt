-- مخطط قاعدة بيانات نظام مراقبة الاعتمادات والمصروفات الحكومية
-- جميع المبالغ مخزنة كأعداد صحيحة بأصغر وحدة نقدية (هللة/فلس) لتجنب أخطاء الكسور العشرية.

DROP TABLE IF EXISTS expenditures;
DROP TABLE IF EXISTS adjustments;
DROP TABLE IF EXISTS appropriations;
DROP TABLE IF EXISTS budget_items;
DROP TABLE IF EXISTS entities;

-- الجهات الحكومية (وزارات، هيئات، ...)
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- أبواب وبنود الميزانية
CREATE TABLE budget_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    chapter     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- الاعتمادات: المبلغ المعتمد لجهة على بند في سنة مالية
CREATE TABLE appropriations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES entities(id),
    item_id         INTEGER NOT NULL REFERENCES budget_items(id),
    fiscal_year     INTEGER NOT NULL,
    original_amount INTEGER NOT NULL CHECK (original_amount >= 0),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_id, item_id, fiscal_year)
);

-- تعديلات الاعتماد (تعزيز / تخفيض / مناقلة)
CREATE TABLE adjustments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    appropriation_id  INTEGER NOT NULL REFERENCES appropriations(id),
    kind              TEXT NOT NULL CHECK (kind IN ('increase', 'decrease')),
    amount            INTEGER NOT NULL CHECK (amount > 0),
    reason            TEXT NOT NULL,
    transfer_group    TEXT,
    adj_date          TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- المصروفات المحملة على الاعتمادات
CREATE TABLE expenditures (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    appropriation_id  INTEGER NOT NULL REFERENCES appropriations(id),
    amount            INTEGER NOT NULL CHECK (amount > 0),
    description       TEXT NOT NULL,
    document_no       TEXT NOT NULL,
    beneficiary       TEXT,
    exp_date          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_exp_appropriation ON expenditures(appropriation_id);
CREATE INDEX idx_adj_appropriation ON adjustments(appropriation_id);
