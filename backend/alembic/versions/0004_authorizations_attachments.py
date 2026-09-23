"""التفويضات وتوزيعها، والمرفقات الإلكترونية.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE authorizations (
  id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id               UUID NOT NULL REFERENCES fiscal_years(id),
  auth_no                      VARCHAR(50) NOT NULL,
  auth_type                    VARCHAR(15) NOT NULL CHECK (auth_type IN ('FINANCIAL', 'DEPARTMENTAL', 'OTHER')),
  auth_date                    DATE NOT NULL,
  entity_id                    UUID NOT NULL REFERENCES entities(id),
  period_from                  DATE,
  period_to                    DATE,
  amount                       NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  purpose                      TEXT NOT NULL CHECK (length(trim(purpose)) > 0),
  funding_source_id            UUID REFERENCES funding_sources(id),
  over_allocation_reason       TEXT,
  over_allocation_approved_by  UUID REFERENCES users(id),
  status                       VARCHAR(12) NOT NULL DEFAULT 'DRAFT' CHECK (status IN (
                                 'DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'POSTED', 'REJECTED', 'RETURNED',
                                 'CANCELLED', 'REVERSED')),
  created_by                   UUID NOT NULL REFERENCES users(id),
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at                 TIMESTAMPTZ,
  posted_by                    UUID REFERENCES users(id),
  posted_at                    TIMESTAMPTZ,
  row_version                  INT NOT NULL DEFAULT 1,
  legacy_ref                   VARCHAR(100),
  is_historical_exception      BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (fiscal_year_id, auth_type, auth_no),
  CHECK (period_to IS NULL OR period_from IS NULL OR period_to >= period_from)
);
CREATE INDEX ix_authorizations_status ON authorizations (status);

CREATE TABLE authorization_allocations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  authorization_id  UUID NOT NULL REFERENCES authorizations(id) ON DELETE CASCADE,
  budget_line_id    UUID NOT NULL REFERENCES budget_lines(id),
  amount            NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  UNIQUE (authorization_id, budget_line_id)
);

CREATE TRIGGER trg_auth_protect BEFORE UPDATE OR DELETE ON authorizations
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document();
CREATE TRIGGER trg_auth_alloc_protect BEFORE INSERT OR UPDATE OR DELETE ON authorization_allocations
  FOR EACH ROW EXECUTE FUNCTION protect_posted_document_lines('authorizations', 'authorization_id');

-- التوزيع لا يتجاوز قيمة التفويض عند الترحيل إلا بموافقة مسجلة (FR-AU-03)
CREATE FUNCTION authorization_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total NUMERIC;
BEGIN
  IF NEW.status = 'POSTED' AND OLD.status <> 'POSTED' THEN
    SELECT coalesce(sum(amount), 0) INTO total FROM authorization_allocations WHERE authorization_id = NEW.id;
    IF total > NEW.amount AND NEW.over_allocation_approved_by IS NULL THEN
      RAISE EXCEPTION 'مجموع التوزيعات (%) يتجاوز قيمة التفويض (%)', total, NEW.amount USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_auth_alloc_guard BEFORE UPDATE ON authorizations
  FOR EACH ROW EXECUTE FUNCTION authorization_allocation_guard();

-- ============================================================================
-- المرفقات
-- ============================================================================
CREATE TABLE attachments (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  storage_key        VARCHAR(300) NOT NULL UNIQUE,
  original_filename  VARCHAR(255) NOT NULL,
  mime_type          VARCHAR(100) NOT NULL,
  size_bytes         BIGINT NOT NULL CHECK (size_bytes > 0),
  sha256             CHAR(64) NOT NULL,
  category           VARCHAR(50),
  description        TEXT,
  ocr_text           TEXT,
  ocr_status         VARCHAR(20),
  uploaded_by        UUID NOT NULL REFERENCES users(id),
  uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_locked          BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_attachments_sha ON attachments (sha256);

CREATE TABLE attachment_links (
  attachment_id  UUID NOT NULL REFERENCES attachments(id),
  source_type    VARCHAR(40) NOT NULL,
  source_id      UUID NOT NULL,
  is_locked      BOOLEAN NOT NULL DEFAULT FALSE,
  linked_by      UUID NOT NULL REFERENCES users(id),
  linked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (attachment_id, source_type, source_id)
);
CREATE INDEX ix_attachment_links_source ON attachment_links (source_type, source_id);

-- المرفق المقفل (مرتبط بمستند مرحّل) لا يُحذف ولا يُستبدل (FR-DOC-03)
CREATE FUNCTION protect_locked_attachment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND OLD.is_locked THEN
    RAISE EXCEPTION 'المرفق مقفل لارتباطه بمستند مرحّل' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.is_locked AND (NEW.is_locked = FALSE
     OR (to_jsonb(NEW) ->> 'storage_key') IS DISTINCT FROM (to_jsonb(OLD) ->> 'storage_key')
     OR (to_jsonb(NEW) ->> 'sha256') IS DISTINCT FROM (to_jsonb(OLD) ->> 'sha256')) THEN
    RAISE EXCEPTION 'المرفق مقفل لارتباطه بمستند مرحّل' USING ERRCODE = '42501';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER trg_attachment_protect BEFORE UPDATE OR DELETE ON attachments
  FOR EACH ROW EXECUTE FUNCTION protect_locked_attachment();
CREATE TRIGGER trg_attachment_link_protect BEFORE UPDATE OR DELETE ON attachment_links
  FOR EACH ROW EXECUTE FUNCTION protect_locked_attachment();

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['authorizations', 'authorization_allocations', 'attachments', 'attachment_links']
  LOOP
    EXECUTE format('CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                   'FOR EACH ROW EXECUTE FUNCTION audit_row()', t);
  END LOOP;
END $$;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS attachment_links, attachments, authorization_allocations, authorizations CASCADE;
DROP FUNCTION IF EXISTS protect_locked_attachment, authorization_allocation_guard CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
