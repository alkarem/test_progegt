"""محرك التنبيهات ومركز الإشعارات، وتطبيع النص العربي للبحث الشامل.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE alert_rules (
  code        VARCHAR(50) PRIMARY KEY,
  name        VARCHAR(200) NOT NULL,
  severity    VARCHAR(10) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'HIGH', 'CRITICAL')),
  params      JSONB NOT NULL DEFAULT '{}',
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE alerts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_code       VARCHAR(50) NOT NULL REFERENCES alert_rules(code),
  severity        VARCHAR(10) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'HIGH', 'CRITICAL')),
  status          VARCHAR(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED')),
  fiscal_year_id  UUID REFERENCES fiscal_years(id),
  budget_line_id  UUID REFERENCES budget_lines(id),
  source_type     VARCHAR(40),
  source_id       UUID,
  message         TEXT NOT NULL,
  details         JSONB,
  dedup_key       VARCHAR(200) NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_by UUID REFERENCES users(id),
  acknowledged_at TIMESTAMPTZ,
  resolved_by     UUID REFERENCES users(id),
  resolved_at     TIMESTAMPTZ,
  resolution      TEXT
);
CREATE UNIQUE INDEX ux_alert_open_dedup ON alerts (dedup_key) WHERE status IN ('OPEN', 'ACKNOWLEDGED');
CREATE INDEX ix_alerts_status ON alerts (status, severity);
CREATE INDEX ix_alerts_line ON alerts (budget_line_id);

CREATE TABLE notifications (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  alert_id    UUID REFERENCES alerts(id),
  title       VARCHAR(200) NOT NULL,
  body        TEXT,
  link        VARCHAR(300),
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_notifications_user ON notifications (user_id, read_at, created_at DESC);

CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON alert_rules FOR EACH ROW EXECUTE FUNCTION audit_row();
CREATE TRIGGER trg_audit AFTER UPDATE OR DELETE ON alerts FOR EACH ROW EXECUTE FUNCTION audit_row();

SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true),
       set_config('gbcfms.reason', 'migration 0008', true);
INSERT INTO alert_rules (code, name, severity, params) VALUES
  ('BUDGET_EXCEEDED',            'تجاوز الاعتماد (رصيد متاح سالب)',             'CRITICAL', '{}'),
  ('LOW_BALANCE',                'انخفاض الرصيد المتاح (نسبة الاستخدام)',        'WARNING',  '{"warning": 80, "high": 90}'),
  ('DUPLICATE_DOCUMENT',         'مستند مكرر محتمل (المستفيد والمبلغ والتاريخ)', 'HIGH',     '{"days": 3}'),
  ('UNUSUAL_TRANSACTION',        'مبلغ غير معتاد للبند',                          'WARNING',  '{"z": 3.5, "min_history": 5}'),
  ('MISSING_ATTACHMENT',         'مستند قُدّم بلا مرفقات',                        'WARNING',  '{}'),
  ('APPROVAL_OVERDUE',           'موافقة متأخرة عن المهلة',                       'WARNING',  '{}'),
  ('STALE_COMMITMENT',           'ارتباط لم تتم تسويته منذ مدة',                  'WARNING',  '{"days": 90}'),
  ('UNALLOCATED_AUTHORIZATION',  'تفويض غير موزع بالكامل',                        'INFO',     '{"days": 15}'),
  ('PENDING_TRANSFER',           'مناقلة لم تكتمل موافقتها',                      'INFO',     '{"days": 7}'),
  ('BALANCE_MISMATCH',           'عدم تطابق الأرصدة مع القيود',                   'CRITICAL', '{}');

-- تطبيع عربي في قاعدة البيانات مطابق لـ app/shared/arabic.py (FR-SR-02)
CREATE FUNCTION ar_normalize(t TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT lower(regexp_replace(
           translate(regexp_replace(coalesce(t, ''), '[ًٌٍَُِّْٰـ]', '', 'g'),
                     'أإآٱةىؤئ٠١٢٣٤٥٦٧٨٩', 'ااااهيوي0123456789'),
           '\s+', ' ', 'g'))
$$;
CREATE INDEX ix_expenditures_desc_trgm ON expenditures USING gin (ar_normalize(description) gin_trgm_ops);
"""

DOWNGRADE = r"""
DROP INDEX IF EXISTS ix_expenditures_desc_trgm;
DROP FUNCTION IF EXISTS ar_normalize;
DROP TABLE IF EXISTS notifications, alerts, alert_rules CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
