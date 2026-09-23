"""المساعد الذكي (12-ai) وقواعد الأنماط غير المعتادة الحتمية (12-ai §3).

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UPGRADE = r"""
SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true);
INSERT INTO alert_rules (code, name, severity, params) VALUES
  ('SPLIT_PURCHASE',             'تجزئة مشتريات محتملة (نفس المستفيد والبند خلال أيام)', 'HIGH',
   '{"days": 7, "threshold": "5000"}'),
  ('ROUND_AMOUNTS',              'مبالغ مستديرة متكررة لنفس المستفيد',                  'INFO',
   '{"multiple": "1000", "min_amount": "5000", "min_count": 3}'),
  ('EXPENSE_WITHOUT_COMMITMENT', 'صرف كبير بلا ارتباط على بند يُتوقع فيه ارتباط',        'WARNING',
   '{"threshold": "10000", "item_codes": ["2/16", "2/18"]}'),
  ('YEAR_END_SPIKE',             'ارتفاع الصرف في نهاية السنة',                          'WARNING',
   '{"days": 15, "factor": "2"}');

-- سجل تفاعلات المساعد: سؤال، أدوات، إجابة، ونتيجة التحقق من الأرقام. للإضافة فقط.
CREATE TABLE ai_interactions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id),
  fiscal_year_id  UUID REFERENCES fiscal_years(id),
  kind            VARCHAR(10) NOT NULL CHECK (kind IN ('ASK', 'SUMMARY')),
  question        TEXT NOT NULL,
  tool_calls      JSONB NOT NULL DEFAULT '[]',
  answer          TEXT,
  status          VARCHAR(12) NOT NULL CHECK (status IN ('OK', 'UNGROUNDED', 'REFUSED', 'ERROR')),
  grounding       JSONB,
  model           VARCHAR(60),
  input_tokens    INTEGER,
  output_tokens   INTEGER,
  duration_ms     INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_ai_interactions_user_time ON ai_interactions (user_id, created_at DESC);
CREATE TRIGGER trg_ai_interactions_immutable BEFORE UPDATE OR DELETE ON ai_interactions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
"""

DOWNGRADE = """
DROP TABLE IF EXISTS ai_interactions;
DELETE FROM alert_rules WHERE code IN ('SPLIT_PURCHASE', 'ROUND_AMOUNTS', 'EXPENSE_WITHOUT_COMMITMENT', 'YEAR_END_SPIKE');
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
