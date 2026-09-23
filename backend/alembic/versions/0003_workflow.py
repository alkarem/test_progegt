"""محرك الموافقات: التعريفات والمراحل والمثيلات والإجراءات والتفويض المؤقت (05-workflow).

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE TABLE workflow_definitions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code         VARCHAR(50) NOT NULL UNIQUE,
  source_type  VARCHAR(40) NOT NULL,
  name         VARCHAR(200) NOT NULL,
  separate_approvers BOOLEAN NOT NULL DEFAULT TRUE,   -- WF-02: من اعتمد مرحلة لا يعتمد التالية
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  version      INT NOT NULL DEFAULT 1
);

CREATE TABLE workflow_steps (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  definition_id        UUID NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
  seq                  SMALLINT NOT NULL CHECK (seq > 0),
  code                 VARCHAR(40) NOT NULL,
  name                 VARCHAR(100) NOT NULL,
  action               VARCHAR(20) NOT NULL CHECK (action IN ('review', 'control', 'supervise', 'approve')),
  min_amount           NUMERIC(18,3),
  max_amount           NUMERIC(18,3),
  runs_budget_check    BOOLEAN NOT NULL DEFAULT FALSE,
  posts                BOOLEAN NOT NULL DEFAULT FALSE,
  sla_days             SMALLINT NOT NULL DEFAULT 3 CHECK (sla_days > 0),
  UNIQUE (definition_id, seq),
  CHECK (min_amount IS NULL OR max_amount IS NULL OR max_amount >= min_amount)
);

CREATE TABLE workflow_instances (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  definition_id    UUID NOT NULL REFERENCES workflow_definitions(id),
  source_type      VARCHAR(40) NOT NULL,
  source_id        UUID NOT NULL,
  fiscal_year_id   UUID NOT NULL REFERENCES fiscal_years(id),
  current_step_id  UUID REFERENCES workflow_steps(id),
  state            VARCHAR(12) NOT NULL CHECK (state IN ('ACTIVE', 'RETURNED', 'COMPLETED', 'REJECTED', 'CANCELLED')),
  round            INT NOT NULL DEFAULT 1,
  amount           NUMERIC(18,3) NOT NULL,
  created_by       UUID NOT NULL REFERENCES users(id),
  step_entered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  UNIQUE (source_type, source_id)
);
CREATE INDEX ix_wf_inst_active ON workflow_instances (current_step_id) WHERE state = 'ACTIVE';

CREATE TABLE workflow_actions (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instance_id     UUID NOT NULL REFERENCES workflow_instances(id),
  round           INT NOT NULL,
  step_id         UUID REFERENCES workflow_steps(id),
  action          VARCHAR(12) NOT NULL CHECK (action IN ('SUBMIT', 'APPROVE', 'REJECT', 'RETURN', 'CANCEL', 'POST', 'SKIP')),
  actor_id        UUID NOT NULL REFERENCES users(id),
  on_behalf_of    UUID REFERENCES users(id),
  comment         TEXT,
  budget_check    JSONB,
  ip              TEXT,
  acted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_wf_actions_instance ON workflow_actions (instance_id, id);
CREATE TRIGGER trg_wf_actions_immutable BEFORE UPDATE OR DELETE ON workflow_actions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE delegations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  delegator_id  UUID NOT NULL REFERENCES users(id),
  delegate_id   UUID NOT NULL REFERENCES users(id),
  valid_from    TIMESTAMPTZ NOT NULL,
  valid_to      TIMESTAMPTZ NOT NULL,
  reason        TEXT NOT NULL,
  revoked_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (delegator_id <> delegate_id),
  CHECK (valid_to > valid_from)
);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['workflow_definitions', 'workflow_steps', 'workflow_instances', 'workflow_actions',
                           'delegations']
  LOOP
    EXECUTE format('CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                   'FOR EACH ROW EXECUTE FUNCTION audit_row()', t);
  END LOOP;
END $$;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS delegations, workflow_actions, workflow_instances, workflow_steps, workflow_definitions CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
