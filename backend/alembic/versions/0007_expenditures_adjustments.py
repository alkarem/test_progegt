"""المصروف الفعلي، والتسويات والقيود العكسية وإلغاء الارتباطات (FR-EX، FR-RV).

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COMMON = r"""
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
"""

UPGRADE = rf"""
CREATE TABLE expenditures (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id      UUID NOT NULL REFERENCES fiscal_years(id),
  document_no         VARCHAR(50) NOT NULL,
  document_type       VARCHAR(30) NOT NULL DEFAULT 'PAYMENT_VOUCHER',
  expenditure_date    DATE NOT NULL,
  date_is_estimated   BOOLEAN NOT NULL DEFAULT FALSE,
  budget_line_id      UUID NOT NULL REFERENCES budget_lines(id),
  supplier_id         UUID REFERENCES suppliers(id),
  commitment_id       UUID REFERENCES commitments(id),
  expense_type        VARCHAR(50),
  amount              NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  payment_method      VARCHAR(20) NOT NULL CHECK (payment_method IN (
                        'CHEQUE', 'BANK_TRANSFER', 'CASH', 'DEPOSIT_ACCOUNT', 'OTHER')),
  payment_order_no    VARCHAR(50),
  cheque_no           VARCHAR(50),
  description         TEXT NOT NULL CHECK (length(trim(description)) > 0),
  notes               TEXT,
{COMMON}
  CHECK (payment_method <> 'CHEQUE' OR cheque_no IS NOT NULL)
);
-- منع تكرار رقم المستند لنفس النوع في السنة، باستثناء الملغى والمرفوض (FR-EX-05)
CREATE UNIQUE INDEX ux_expenditures_docno ON expenditures (fiscal_year_id, document_type, document_no)
  WHERE status NOT IN ('CANCELLED', 'REJECTED');
CREATE INDEX ix_expenditures_line ON expenditures (budget_line_id);
CREATE INDEX ix_expenditures_supplier ON expenditures (supplier_id, expenditure_date);
CREATE INDEX ix_expenditures_commitment ON expenditures (commitment_id) WHERE commitment_id IS NOT NULL;
CREATE TRIGGER trg_expenditure_protect BEFORE UPDATE OR DELETE ON expenditures
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document();

CREATE TABLE adjustments (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id       UUID NOT NULL REFERENCES fiscal_years(id),
  adjustment_no        VARCHAR(50) NOT NULL,
  adjustment_date      DATE NOT NULL,
  kind                 VARCHAR(25) NOT NULL CHECK (kind IN ('ADJUSTMENT', 'REVERSAL', 'COMMITMENT_CANCELLATION')),
  reverses_source_type VARCHAR(40),
  reverses_source_id   UUID,
  commitment_id        UUID REFERENCES commitments(id),
  cancel_amount        NUMERIC(18,3) CHECK (cancel_amount IS NULL OR cancel_amount > 0),
  reason               TEXT NOT NULL CHECK (length(trim(reason)) >= 5),
{COMMON}
  UNIQUE (fiscal_year_id, adjustment_no),
  CHECK ((kind = 'REVERSAL') = (reverses_source_id IS NOT NULL AND reverses_source_type IS NOT NULL)),
  CHECK ((kind = 'COMMITMENT_CANCELLATION') = (commitment_id IS NOT NULL))
);
-- قيد عكسي واحد نشط لكل مستند
CREATE UNIQUE INDEX ux_adjustments_reversal ON adjustments (reverses_source_type, reverses_source_id)
  WHERE kind = 'REVERSAL' AND status NOT IN ('CANCELLED', 'REJECTED');

CREATE TABLE adjustment_lines (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  adjustment_id   UUID NOT NULL REFERENCES adjustments(id) ON DELETE CASCADE,
  budget_line_id  UUID NOT NULL REFERENCES budget_lines(id),
  component       VARCHAR(15) NOT NULL CHECK (component IN ('APPROPRIATION', 'ALLOCATION', 'ACTUAL')),
  direction       SMALLINT NOT NULL CHECK (direction IN (-1, 1)),
  amount          NUMERIC(18,3) NOT NULL CHECK (amount > 0)
);
CREATE TRIGGER trg_adjustment_protect BEFORE UPDATE OR DELETE ON adjustments
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document();
CREATE TRIGGER trg_adjustment_lines_protect BEFORE INSERT OR UPDATE OR DELETE ON adjustment_lines
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document_lines('adjustments', 'adjustment_id');

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['expenditures', 'adjustments', 'adjustment_lines']
  LOOP
    EXECUTE format('CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                   'FOR EACH ROW EXECUTE FUNCTION audit_row()', t);
  END LOOP;
END $$;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS adjustment_lines, adjustments, expenditures CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
