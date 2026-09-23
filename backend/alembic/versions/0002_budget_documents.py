"""مستندات الميزانية (اعتماد أصلي، تعزيز، تخفيض) وترقيم المستندات.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = r"""
-- ترقيم تلقائي لكل (سنة، نوع مستند)
CREATE TABLE document_sequences (
  fiscal_year_id  UUID NOT NULL REFERENCES fiscal_years(id),
  doc_type        VARCHAR(40) NOT NULL,
  last_no         INT NOT NULL DEFAULT 0 CHECK (last_no >= 0),
  PRIMARY KEY (fiscal_year_id, doc_type)
);

CREATE TABLE budget_documents (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id           UUID NOT NULL REFERENCES fiscal_years(id),
  kind                     VARCHAR(20) NOT NULL
                           CHECK (kind IN ('ORIGINAL_BUDGET', 'BUDGET_INCREASE', 'BUDGET_DECREASE')),
  doc_no                   VARCHAR(50) NOT NULL,
  doc_date                 DATE NOT NULL,
  reference                VARCHAR(100),
  description              TEXT NOT NULL CHECK (length(trim(description)) > 0),
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
  UNIQUE (fiscal_year_id, kind, doc_no)
);
CREATE INDEX ix_budget_documents_status ON budget_documents (status);

CREATE TABLE budget_document_lines (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id     UUID NOT NULL REFERENCES budget_documents(id) ON DELETE CASCADE,
  budget_line_id  UUID NOT NULL REFERENCES budget_lines(id),
  amount          NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  UNIQUE (document_id, budget_line_id)
);

-- المستند المرحّل لا يُعدَّل ولا تُعدَّل أسطره (FR-RV-01)؛ يُسمح فقط بتغيير الحالة إلى REVERSED
CREATE FUNCTION protect_posted_document() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status NOT IN ('DRAFT', 'RETURNED') THEN
      RAISE EXCEPTION 'لا يُحذف مستند بعد تقديمه؛ استخدم الإلغاء' USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status IN ('POSTED', 'REVERSED', 'CANCELLED', 'REJECTED') THEN
    IF NOT (OLD.status = 'POSTED' AND NEW.status = 'REVERSED'
            AND (to_jsonb(NEW) - 'status' - 'updated_at' - 'row_version')
              = (to_jsonb(OLD) - 'status' - 'updated_at' - 'row_version')) THEN
      RAISE EXCEPTION 'المستند في حالة نهائية (%) ولا يقبل التعديل', OLD.status USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION protect_posted_document_lines() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st TEXT; doc UUID; parent_table TEXT := TG_ARGV[0]; parent_col TEXT := TG_ARGV[1];
BEGIN
  doc := CASE WHEN TG_OP = 'DELETE' THEN (to_jsonb(OLD) ->> parent_col)::uuid
              ELSE (to_jsonb(NEW) ->> parent_col)::uuid END;
  EXECUTE format('SELECT status FROM %I WHERE id = $1', parent_table) INTO st USING doc;
  IF st IS NOT NULL AND st NOT IN ('DRAFT', 'RETURNED') THEN
    RAISE EXCEPTION 'أسطر المستند مقفلة بعد تقديمه (الحالة %)', st USING ERRCODE = '42501';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER trg_bd_protect BEFORE UPDATE OR DELETE ON budget_documents
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document();
CREATE TRIGGER trg_bdl_protect BEFORE INSERT OR UPDATE OR DELETE ON budget_document_lines
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document_lines('budget_documents', 'document_id');

CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON budget_documents
  FOR EACH ROW EXECUTE FUNCTION audit_row();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON budget_document_lines
  FOR EACH ROW EXECUTE FUNCTION audit_row();
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS budget_document_lines, budget_documents, document_sequences CASCADE;
DROP FUNCTION IF EXISTS protect_posted_document_lines, protect_posted_document CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
