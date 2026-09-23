"""محرك استيراد Excel: الدفعات والصفوف والمشاكل (10-excel-import).

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE import_batches (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename         VARCHAR(255) NOT NULL,
  sha256           CHAR(64) NOT NULL,
  attachment_id    UUID REFERENCES attachments(id),
  fiscal_year_id   UUID REFERENCES fiscal_years(id),
  entity_id        UUID REFERENCES entities(id),
  status           VARCHAR(12) NOT NULL DEFAULT 'UPLOADED' CHECK (status IN (
                     'UPLOADED', 'ANALYZED', 'VALIDATED', 'IMPORTED', 'FAILED', 'DISCARDED')),
  mapping          JSONB,
  decisions        JSONB NOT NULL DEFAULT '{}',
  stats            JSONB,
  created_by       UUID NOT NULL REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  imported_by      UUID REFERENCES users(id),
  imported_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_import_sha_imported ON import_batches (sha256) WHERE status = 'IMPORTED';

CREATE TABLE import_rows (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id        UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  sheet_name      VARCHAR(100) NOT NULL,
  row_no          INT NOT NULL,
  raw             JSONB NOT NULL,
  parsed          JSONB,
  classification  VARCHAR(40),
  excel_computed  JSONB,
  target_type     VARCHAR(40),
  target_id       UUID,
  status          VARCHAR(12) NOT NULL DEFAULT 'PENDING',
  UNIQUE (batch_id, sheet_name, row_no)
);

CREATE TABLE import_issues (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id    UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  row_id      UUID REFERENCES import_rows(id) ON DELETE CASCADE,
  severity    VARCHAR(10) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'HIGH', 'CRITICAL')),
  code        VARCHAR(20) NOT NULL,
  location    VARCHAR(120),
  message     TEXT NOT NULL,
  decision    VARCHAR(20),
  blocking    BOOLEAN NOT NULL DEFAULT FALSE,
  details     JSONB
);
CREATE INDEX ix_import_issues_batch ON import_issues (batch_id, severity);

CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON import_batches FOR EACH ROW EXECUTE FUNCTION audit_row();
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS import_issues, import_rows, import_batches CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
