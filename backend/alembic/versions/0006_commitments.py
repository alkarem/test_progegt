"""الحجوزات والارتباطات (FR-CM). يُضاف ربط القيد بالارتباط لحساب القائم من القيود.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE commitments (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id           UUID NOT NULL REFERENCES fiscal_years(id),
  commitment_no            VARCHAR(50) NOT NULL,
  commitment_type          VARCHAR(20) NOT NULL CHECK (commitment_type IN (
                             'PURCHASE_REQUEST', 'PURCHASE_ORDER', 'CONTRACT', 'OBLIGATION', 'OTHER')),
  commitment_status        VARCHAR(16) NOT NULL DEFAULT 'DRAFT' CHECK (commitment_status IN (
                             'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'PARTIALLY_PAID', 'FULLY_PAID',
                             'CANCELLED', 'CLOSED')),
  commitment_date          DATE NOT NULL,
  budget_line_id           UUID NOT NULL REFERENCES budget_lines(id),
  supplier_id              UUID REFERENCES suppliers(id),
  parent_id                UUID REFERENCES commitments(id),
  amount                   NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  description              TEXT NOT NULL CHECK (length(trim(description)) > 0),
  reference                VARCHAR(100),
  expected_completion      DATE,
  carried_from_id          UUID REFERENCES commitments(id),
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
  UNIQUE (fiscal_year_id, commitment_type, commitment_no),
  CHECK (parent_id IS DISTINCT FROM id)
);
CREATE INDEX ix_commitments_line ON commitments (budget_line_id);
CREATE INDEX ix_commitments_status ON commitments (commitment_status);
CREATE UNIQUE INDEX ux_commitments_parent_active ON commitments (parent_id)
  WHERE parent_id IS NOT NULL AND status NOT IN ('CANCELLED', 'REJECTED');

-- حالة الارتباط التجارية تتغير بعد الترحيل (مسدد جزئيًا/كليًا، ملغى، مقفل) دون تعديل بياناته
CREATE FUNCTION protect_posted_commitment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status NOT IN ('DRAFT', 'RETURNED') THEN
      RAISE EXCEPTION 'لا يُحذف مستند بعد تقديمه؛ استخدم الإلغاء' USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status IN ('POSTED', 'REVERSED', 'CANCELLED', 'REJECTED') THEN
    IF (to_jsonb(NEW) - 'status' - 'commitment_status' - 'updated_at' - 'row_version')
       <> (to_jsonb(OLD) - 'status' - 'commitment_status' - 'updated_at' - 'row_version')
       OR (NEW.status <> OLD.status AND NOT (OLD.status = 'POSTED' AND NEW.status = 'REVERSED')) THEN
      RAISE EXCEPTION 'المستند في حالة نهائية (%) ولا يقبل التعديل', OLD.status USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_commitment_protect BEFORE UPDATE OR DELETE ON commitments
  FOR EACH ROW EXECUTE FUNCTION protect_posted_commitment();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON commitments FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE ledger_entries ADD COLUMN commitment_id UUID REFERENCES commitments(id);
CREATE INDEX ix_le_commitment ON ledger_entries (commitment_id) WHERE commitment_id IS NOT NULL;

-- القائم لكل ارتباط لا يصبح سالبًا (INV-04 على مستوى المستند)
CREATE FUNCTION ledger_check_commitment_outstanding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s NUMERIC;
BEGIN
  SELECT coalesce(sum(direction * amount), 0) INTO s FROM ledger_entries
   WHERE commitment_id = NEW.commitment_id AND component = NEW.component;
  IF s < 0 THEN
    RAISE EXCEPTION 'الرصيد القائم للارتباط لا يمكن أن يصبح سالبًا' USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_le_commitment_outstanding AFTER INSERT ON ledger_entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  WHEN (NEW.commitment_id IS NOT NULL AND NEW.component IN ('COMMITMENT', 'RESERVATION'))
  EXECUTE FUNCTION ledger_check_commitment_outstanding();
"""

DOWNGRADE = r"""
DROP TRIGGER IF EXISTS trg_le_commitment_outstanding ON ledger_entries;
ALTER TABLE ledger_entries DROP COLUMN IF EXISTS commitment_id;
DROP TABLE IF EXISTS commitments CASCADE;
DROP FUNCTION IF EXISTS ledger_check_commitment_outstanding, protect_posted_commitment CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
