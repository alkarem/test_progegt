"""النسخ الاحتياطي والاستعادة (15-deployment §2).

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE backup_settings (
  id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  location            VARCHAR(500) NOT NULL DEFAULT 'storage/backups',
  daily_time          VARCHAR(5) NOT NULL DEFAULT '02:00' CHECK (daily_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
  enabled             BOOLEAN NOT NULL DEFAULT TRUE,
  retention_count     SMALLINT NOT NULL DEFAULT 30 CHECK (retention_count BETWEEN 1 AND 365),
  include_attachments BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
SELECT set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true);
INSERT INTO backup_settings (id) VALUES (1);

CREATE TABLE backups (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind          VARCHAR(12) NOT NULL CHECK (kind IN ('MANUAL', 'SCHEDULED', 'PRE_RESTORE')),
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  location      VARCHAR(500),
  file_name     VARCHAR(255),
  size_bytes    BIGINT,
  sha256        CHAR(64),
  encrypted     BOOLEAN NOT NULL DEFAULT FALSE,
  status        VARCHAR(12) NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'DELETED')),
  verified_at   TIMESTAMPTZ,
  verification  JSONB,
  manifest      JSONB,
  requested_by  UUID REFERENCES users(id),
  error         TEXT
);
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON backup_settings FOR EACH ROW EXECUTE FUNCTION audit_row();
"""

DOWNGRADE = "DROP TABLE IF EXISTS backups, backup_settings CASCADE;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
