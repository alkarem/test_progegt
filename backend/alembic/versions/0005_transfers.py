"""المناقلات بين البنود (FR-TR).

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE transfers (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id           UUID NOT NULL REFERENCES fiscal_years(id),
  transfer_no              VARCHAR(50) NOT NULL,
  transfer_date            DATE NOT NULL,
  reason                   TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  approval_no              VARCHAR(50),
  approved_by              UUID REFERENCES users(id),
  status                   VARCHAR(12) NOT NULL DEFAULT 'DRAFT' CHECK (status IN (
                             'DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'POSTED', 'REJECTED', 'RETURNED',
                             'CANCELLED', 'REVERSED')),
  created_by               UUID NOT NULL REFERENCES users(id),
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at             TIMESTAMPTZ,
  posted_by                UUID REFERENCES users(id),
  posted_at                TIMESTAMPTZ,
  row_version              INT NOT NULL DEFAULT 1,
  legacy_ref               VARCHAR(100),
  is_historical_exception  BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (fiscal_year_id, transfer_no)
);
CREATE INDEX ix_transfers_status ON transfers (status);

CREATE TABLE transfer_lines (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transfer_id   UUID NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
  from_line_id  UUID NOT NULL REFERENCES budget_lines(id),
  to_line_id    UUID NOT NULL REFERENCES budget_lines(id),
  amount        NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  CHECK (from_line_id <> to_line_id)
);
CREATE INDEX ix_transfer_lines_transfer ON transfer_lines (transfer_id);

-- طرفا المناقلة في سنة المستند نفسها
CREATE FUNCTION transfer_line_same_year() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fy UUID; a UUID; b UUID;
BEGIN
  SELECT fiscal_year_id INTO fy FROM transfers WHERE id = NEW.transfer_id;
  SELECT fiscal_year_id INTO a FROM budget_lines WHERE id = NEW.from_line_id;
  SELECT fiscal_year_id INTO b FROM budget_lines WHERE id = NEW.to_line_id;
  IF a <> fy OR b <> fy THEN
    RAISE EXCEPTION 'المناقلة مسموحة فقط داخل السنة المالية للمستند' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_transfer_line_year BEFORE INSERT OR UPDATE ON transfer_lines
  FOR EACH ROW EXECUTE FUNCTION transfer_line_same_year();

CREATE TRIGGER trg_transfer_protect BEFORE UPDATE OR DELETE ON transfers
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document();
CREATE TRIGGER trg_transfer_lines_protect BEFORE INSERT OR UPDATE OR DELETE ON transfer_lines
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document_lines('transfers', 'transfer_id');
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON transfers FOR EACH ROW EXECUTE FUNCTION audit_row();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON transfer_lines FOR EACH ROW EXECUTE FUNCTION audit_row();
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS transfer_lines, transfers CASCADE;
DROP FUNCTION IF EXISTS transfer_line_same_year CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
