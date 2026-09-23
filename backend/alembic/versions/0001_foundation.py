"""الأساس: المستخدمون والصلاحيات، والبيانات المرجعية، ودفتر الحركات، والأرصدة، والتدقيق.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

-- ============================================================================
-- التدقيق (يُنشأ أولًا لأن كل الجداول تعتمد على trigger التدقيق)
-- ============================================================================
CREATE TABLE audit_log (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at     TIMESTAMPTZ NOT NULL,
  user_id         UUID,
  ip              TEXT,
  user_agent      TEXT,
  request_id      TEXT,
  action          VARCHAR(40) NOT NULL,
  table_name      VARCHAR(63),
  record_id       TEXT,
  old_values      JSONB,
  new_values      JSONB,
  changed_fields  TEXT[],
  reason          TEXT,
  prev_hash       CHAR(64),
  row_hash        CHAR(64) NOT NULL
);
CREATE INDEX ix_audit_record    ON audit_log (table_name, record_id);
CREATE INDEX ix_audit_user_time ON audit_log (user_id, occurred_at);
CREATE INDEX ix_audit_time      ON audit_log (occurred_at);

-- صيغة Hash موحدة: تستخدمها الإضافة والتحقق معًا
CREATE FUNCTION audit_hash(p_prev TEXT, p_at TIMESTAMPTZ, p_user UUID, p_action TEXT, p_table TEXT,
                           p_record TEXT, p_old JSONB, p_new JSONB, p_reason TEXT)
RETURNS CHAR(64) LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws(E'\x1f',
      coalesce(p_prev, ''), extract(epoch FROM p_at)::text, coalesce(p_user::text, ''),
      p_action, coalesce(p_table, ''), coalesce(p_record, ''),
      coalesce(p_old::text, ''), coalesce(p_new::text, ''), coalesce(p_reason, '')), 'UTF8')), 'hex')
$$;

-- إضافة سجل تدقيق بسلسلة Hash. القفل الاستشاري يسلسل الإضافات حتى نهاية المعاملة.
CREATE FUNCTION audit_append(p_action TEXT, p_table TEXT, p_record TEXT, p_old JSONB, p_new JSONB,
                             p_changed TEXT[] DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE
  v_user   UUID := nullif(current_setting('gbcfms.user_id', true), '')::uuid;
  v_reason TEXT := nullif(current_setting('gbcfms.reason', true), '');
  v_at     TIMESTAMPTZ := clock_timestamp();
  v_prev   CHAR(64);
  v_id     BIGINT;
BEGIN
  PERFORM pg_advisory_xact_lock(724242);
  SELECT row_hash INTO v_prev FROM audit_log ORDER BY id DESC LIMIT 1;
  INSERT INTO audit_log (occurred_at, user_id, ip, user_agent, request_id, action, table_name, record_id,
                         old_values, new_values, changed_fields, reason, prev_hash, row_hash)
  VALUES (v_at, v_user, nullif(current_setting('gbcfms.ip', true), ''),
          nullif(current_setting('gbcfms.user_agent', true), ''),
          nullif(current_setting('gbcfms.request_id', true), ''),
          p_action, p_table, p_record, p_old, p_new, p_changed, v_reason, v_prev,
          audit_hash(v_prev, v_at, v_user, p_action, p_table, p_record, p_old, p_new, v_reason))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- trigger عام لتسجيل كل تغيير. يرفض الكتابة بلا سياق مستخدم.
CREATE FUNCTION audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_old JSONB; v_new JSONB; v_changed TEXT[]; v_rec TEXT;
  v_secret TEXT[] := ARRAY['password_hash', 'token_hash', 'mfa_secret_enc'];
BEGIN
  IF nullif(current_setting('gbcfms.user_id', true), '') IS NULL THEN
    RAISE EXCEPTION 'سياق التدقيق غير مضبوط: لا يُسمح بالكتابة على % دون مستخدم', TG_TABLE_NAME
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP IN ('UPDATE', 'DELETE') THEN v_old := to_jsonb(OLD) - v_secret; END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN v_new := to_jsonb(NEW) - v_secret; END IF;
  IF TG_OP = 'UPDATE' THEN
    SELECT array_agg(k ORDER BY k) INTO v_changed
      FROM (SELECT key AS k FROM jsonb_each(to_jsonb(NEW)) n
             WHERE n.value IS DISTINCT FROM (to_jsonb(OLD) -> n.key)) s;
    IF v_changed IS NULL THEN RETURN NEW; END IF;
  END IF;
  v_rec := coalesce(v_new, v_old) ->> 'id';
  PERFORM audit_append(TG_OP, TG_TABLE_NAME, v_rec, v_old, v_new, v_changed);
  RETURN coalesce(NEW, OLD);
END $$;

-- التحقق من سلامة السلسلة: يعيد أول سجل مكسور (أو NULL إذا كانت سليمة)
CREATE FUNCTION audit_verify_chain() RETURNS TABLE (broken_id BIGINT, checked BIGINT)
LANGUAGE plpgsql AS $$
DECLARE r RECORD; v_prev CHAR(64) := NULL; n BIGINT := 0;
BEGIN
  FOR r IN SELECT * FROM audit_log ORDER BY id LOOP
    n := n + 1;
    IF r.prev_hash IS DISTINCT FROM v_prev
       OR r.row_hash <> audit_hash(r.prev_hash, r.occurred_at, r.user_id, r.action, r.table_name,
                                   r.record_id, r.old_values, r.new_values, r.reason) THEN
      broken_id := r.id; checked := n; RETURN NEXT; RETURN;
    END IF;
    v_prev := r.row_hash;
  END LOOP;
  broken_id := NULL; checked := n; RETURN NEXT;
END $$;

CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'السجلات في % غير قابلة للتعديل أو الحذف (استخدم قيدًا عكسيًا أو تسوية)', TG_TABLE_NAME
    USING ERRCODE = '42501';
END $$;

CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

-- ============================================================================
-- المستخدمون والصلاحيات
-- ============================================================================
CREATE TABLE users (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username              CITEXT NOT NULL UNIQUE,
  full_name             VARCHAR(200) NOT NULL,
  email                 CITEXT UNIQUE,
  password_hash         VARCHAR(255) NOT NULL,
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  is_system             BOOLEAN NOT NULL DEFAULT FALSE,
  must_change_password  BOOLEAN NOT NULL DEFAULT TRUE,
  failed_logins         SMALLINT NOT NULL DEFAULT 0,
  locked_until          TIMESTAMPTZ,
  password_changed_at   TIMESTAMPTZ,
  last_login_at         TIMESTAMPTZ,
  auth_version          INT NOT NULL DEFAULT 1,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at            TIMESTAMPTZ,
  CHECK (length(username) BETWEEN 3 AND 60)
);

CREATE TABLE roles (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        VARCHAR(50) NOT NULL UNIQUE,
  name        VARCHAR(100) NOT NULL,
  is_system   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE permissions (
  code        VARCHAR(100) PRIMARY KEY,
  module      VARCHAR(50) NOT NULL,
  action      VARCHAR(50) NOT NULL,
  description TEXT NOT NULL
);

CREATE TABLE role_permissions (
  role_id          UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_code  VARCHAR(100) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_code)
);

CREATE TABLE user_roles (
  user_id  UUID NOT NULL REFERENCES users(id),
  role_id  UUID NOT NULL REFERENCES roles(id),
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE user_scopes (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  scope_type  VARCHAR(20) NOT NULL CHECK (scope_type IN ('FISCAL_YEAR', 'ENTITY', 'CHAPTER', 'ITEM')),
  scope_id    UUID NOT NULL,
  UNIQUE (user_id, scope_type, scope_id)
);

CREATE TABLE refresh_tokens (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id),
  token_hash  CHAR(64) NOT NULL UNIQUE,
  family_id   UUID NOT NULL,
  issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  last_used_at TIMESTAMPTZ,
  revoked_at  TIMESTAMPTZ,
  replaced_by UUID REFERENCES refresh_tokens(id),
  ip          TEXT,
  user_agent  TEXT
);
CREATE INDEX ix_refresh_family ON refresh_tokens (family_id);
CREATE INDEX ix_refresh_user   ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

-- ============================================================================
-- البيانات المرجعية
-- ============================================================================
CREATE TABLE entities (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code       VARCHAR(30) NOT NULL UNIQUE,
  name       VARCHAR(200) NOT NULL,
  parent_id  UUID REFERENCES entities(id),
  is_active  BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE budget_chapters (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code           VARCHAR(10) NOT NULL UNIQUE,
  name           VARCHAR(200) NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  display_order  INT NOT NULL DEFAULT 0
);

CREATE TABLE budget_items (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chapter_id     UUID NOT NULL REFERENCES budget_chapters(id),
  code           VARCHAR(20) NOT NULL,
  name           VARCHAR(200) NOT NULL,
  parent_id      UUID REFERENCES budget_items(id),
  budget_type    VARCHAR(30) NOT NULL DEFAULT 'EXPENSE',
  is_postable    BOOLEAN NOT NULL DEFAULT TRUE,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  display_order  INT NOT NULL DEFAULT 0,
  UNIQUE (chapter_id, code),
  CHECK (code ~ '^[0-9]+(/[0-9]+)+$'),
  CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE fiscal_years (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  year                   SMALLINT NOT NULL UNIQUE CHECK (year BETWEEN 2000 AND 2100),
  start_date             DATE NOT NULL,
  end_date               DATE NOT NULL,
  status                 VARCHAR(12) NOT NULL DEFAULT 'PLANNING'
                         CHECK (status IN ('PLANNING', 'OPEN', 'CLOSING', 'CLOSED')),
  control_basis          VARCHAR(15) NOT NULL DEFAULT 'TWO_LEVEL'
                         CHECK (control_basis IN ('APPROPRIATION', 'AUTHORIZATION', 'TWO_LEVEL')),
  count_reservations     BOOLEAN NOT NULL DEFAULT TRUE,
  carry_forward_item_id  UUID REFERENCES budget_items(id),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date > start_date)
);

CREATE TABLE fiscal_periods (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id  UUID NOT NULL REFERENCES fiscal_years(id),
  period_no       SMALLINT NOT NULL CHECK (period_no BETWEEN 1 AND 13),
  start_date      DATE NOT NULL,
  end_date        DATE NOT NULL,
  status          VARCHAR(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
  closed_by       UUID REFERENCES users(id),
  closed_at       TIMESTAMPTZ,
  UNIQUE (fiscal_year_id, period_no),
  CHECK (end_date >= start_date)
);

CREATE TABLE budget_lines (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fiscal_year_id  UUID NOT NULL REFERENCES fiscal_years(id),
  entity_id       UUID NOT NULL REFERENCES entities(id),
  item_id         UUID NOT NULL REFERENCES budget_items(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (fiscal_year_id, entity_id, item_id)
);

-- ذاكرة الأرصدة: لا يكتب فيها إلا trigger دفتر الحركات
CREATE TABLE budget_balances (
  budget_line_id  UUID PRIMARY KEY REFERENCES budget_lines(id),
  appropriation   NUMERIC(18,3) NOT NULL DEFAULT 0,
  allocation      NUMERIC(18,3) NOT NULL DEFAULT 0,
  transfer_in     NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (transfer_in  >= 0),
  transfer_out    NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (transfer_out >= 0),
  reservation     NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (reservation  >= 0),
  commitment      NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (commitment   >= 0),
  actual          NUMERIC(18,3) NOT NULL DEFAULT 0,
  version         BIGINT NOT NULL DEFAULT 0,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION create_balance_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO budget_balances (budget_line_id) VALUES (NEW.id);
  RETURN NEW;
END $$;
CREATE TRIGGER trg_budget_line_balance AFTER INSERT ON budget_lines
  FOR EACH ROW EXECUTE FUNCTION create_balance_row();

CREATE TABLE funding_sources (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code       VARCHAR(30) NOT NULL UNIQUE,
  name       VARCHAR(200) NOT NULL,
  is_active  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE suppliers (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name             VARCHAR(250) NOT NULL,
  name_normalized  VARCHAR(250) NOT NULL,
  kind             VARCHAR(20) NOT NULL DEFAULT 'COMPANY'
                   CHECK (kind IN ('COMPANY', 'PERSON', 'GOV_ACCOUNT', 'OTHER')),
  tax_no           VARCHAR(50),
  commercial_reg   VARCHAR(50),
  phone            VARCHAR(30),
  notes            TEXT,
  is_active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_suppliers_name_trgm ON suppliers USING gin (name_normalized gin_trgm_ops);

-- ============================================================================
-- منح الاستثناء (D-10)
-- ============================================================================
CREATE TABLE override_grants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id),
  budget_line_id  UUID REFERENCES budget_lines(id),
  max_amount      NUMERIC(18,3) NOT NULL CHECK (max_amount > 0),
  used_amount     NUMERIC(18,3) NOT NULL DEFAULT 0,
  valid_from      TIMESTAMPTZ NOT NULL,
  valid_to        TIMESTAMPTZ NOT NULL,
  reason          TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  granted_by      UUID NOT NULL REFERENCES users(id),
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (used_amount >= 0 AND used_amount <= max_amount),
  CHECK (valid_to > valid_from),
  CHECK (granted_by <> user_id)
);

-- ============================================================================
-- دفتر الحركات
-- ============================================================================
CREATE TABLE ledger_entries (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_no                 BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
  fiscal_year_id           UUID NOT NULL REFERENCES fiscal_years(id),
  period_id                UUID NOT NULL REFERENCES fiscal_periods(id),
  budget_line_id           UUID NOT NULL REFERENCES budget_lines(id),
  txn_type                 VARCHAR(30) NOT NULL CHECK (txn_type IN (
      'ORIGINAL_BUDGET', 'BUDGET_ALLOCATION', 'BUDGET_INCREASE', 'BUDGET_DECREASE',
      'TRANSFER_IN', 'TRANSFER_OUT', 'PRE_COMMITMENT', 'RESERVATION_RELEASE',
      'COMMITMENT', 'COMMITMENT_LIQUIDATION', 'ACTUAL_EXPENDITURE', 'ADJUSTMENT',
      'REVERSAL', 'CANCELLATION', 'CLOSING', 'CARRY_FORWARD')),
  component                VARCHAR(15) NOT NULL CHECK (component IN (
      'APPROPRIATION', 'ALLOCATION', 'TRANSFER_IN', 'TRANSFER_OUT', 'RESERVATION', 'COMMITMENT', 'ACTUAL')),
  direction                SMALLINT NOT NULL CHECK (direction IN (-1, 1)),
  amount                   NUMERIC(18,3) NOT NULL CHECK (amount > 0),
  entry_date               DATE NOT NULL,
  date_is_estimated        BOOLEAN NOT NULL DEFAULT FALSE,
  source_type              VARCHAR(40) NOT NULL,
  source_id                UUID NOT NULL,
  source_line_id           UUID,
  document_no              VARCHAR(50),
  transfer_group_id        UUID,
  reversal_of_id           UUID UNIQUE REFERENCES ledger_entries(id),
  override_grant_id        UUID REFERENCES override_grants(id),
  is_historical_exception  BOOLEAN NOT NULL DEFAULT FALSE,
  description              TEXT,
  posted_by                UUID NOT NULL REFERENCES users(id),
  approved_by              UUID REFERENCES users(id),
  posted_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((txn_type IN ('TRANSFER_IN', 'TRANSFER_OUT')) = (transfer_group_id IS NOT NULL)
         OR (txn_type = 'REVERSAL' AND transfer_group_id IS NOT NULL)),
  CHECK ((txn_type = 'REVERSAL') = (reversal_of_id IS NOT NULL))
);
CREATE INDEX ix_le_line_comp ON ledger_entries (budget_line_id, component);
CREATE INDEX ix_le_year_date ON ledger_entries (fiscal_year_id, entry_date);
CREATE INDEX ix_le_source    ON ledger_entries (source_type, source_id);
CREATE INDEX ix_le_docno     ON ledger_entries (document_no);
CREATE INDEX ix_le_group     ON ledger_entries (transfer_group_id) WHERE transfer_group_id IS NOT NULL;

CREATE TRIGGER trg_le_immutable BEFORE UPDATE OR DELETE ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER trg_le_no_truncate BEFORE TRUNCATE ON ledger_entries
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

-- تحقق قبل الإدراج: السنة والفترة مفتوحتان ومتسقتان مع سطر الميزانية والتاريخ (INV-06)
CREATE FUNCTION ledger_validate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fy RECORD; p RECORD; line_year UUID; orig RECORD;
BEGIN
  SELECT * INTO fy FROM fiscal_years WHERE id = NEW.fiscal_year_id;
  IF fy.status NOT IN ('OPEN', 'CLOSING') THEN
    RAISE EXCEPTION 'السنة المالية % غير مفتوحة للترحيل', fy.year USING ERRCODE = 'P0001';
  END IF;
  IF fy.status = 'CLOSING' AND NEW.txn_type NOT IN ('CLOSING', 'CARRY_FORWARD', 'RESERVATION_RELEASE') THEN
    RAISE EXCEPTION 'السنة المالية % قيد الإقفال: يُسمح بقيود الإقفال فقط', fy.year USING ERRCODE = 'P0001';
  END IF;
  SELECT * INTO p FROM fiscal_periods WHERE id = NEW.period_id;
  IF p.fiscal_year_id <> NEW.fiscal_year_id THEN
    RAISE EXCEPTION 'الفترة لا تنتمي للسنة المالية' USING ERRCODE = 'P0001';
  END IF;
  IF p.status <> 'OPEN' THEN
    RAISE EXCEPTION 'الفترة المالية % مقفلة', p.period_no USING ERRCODE = 'P0001';
  END IF;
  IF NEW.entry_date NOT BETWEEN p.start_date AND p.end_date THEN
    RAISE EXCEPTION 'تاريخ القيد % خارج الفترة المالية', NEW.entry_date USING ERRCODE = 'P0001';
  END IF;
  SELECT fiscal_year_id INTO line_year FROM budget_lines WHERE id = NEW.budget_line_id;
  IF line_year <> NEW.fiscal_year_id THEN
    RAISE EXCEPTION 'سطر الميزانية لا ينتمي للسنة المالية للقيد' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.reversal_of_id IS NOT NULL THEN        -- INV-09
    SELECT * INTO orig FROM ledger_entries WHERE id = NEW.reversal_of_id;
    IF orig.budget_line_id <> NEW.budget_line_id OR orig.component <> NEW.component
       OR orig.amount <> NEW.amount OR orig.direction <> -NEW.direction THEN
      RAISE EXCEPTION 'القيد العكسي لا يطابق القيد الأصلي' USING ERRCODE = 'P0001';
    END IF;
    IF orig.txn_type = 'REVERSAL' THEN
      RAISE EXCEPTION 'لا يُعكس قيد عكسي' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_le_validate BEFORE INSERT ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION ledger_validate();

-- تحديث الأرصدة في المعاملة نفسها (INV-08 بنيويًا)
CREATE FUNCTION ledger_apply_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d NUMERIC(18,3) := NEW.direction * NEW.amount;
BEGIN
  UPDATE budget_balances SET
    appropriation = appropriation + CASE WHEN NEW.component = 'APPROPRIATION' THEN d ELSE 0 END,
    allocation    = allocation    + CASE WHEN NEW.component = 'ALLOCATION'    THEN d ELSE 0 END,
    transfer_in   = transfer_in   + CASE WHEN NEW.component = 'TRANSFER_IN'   THEN d ELSE 0 END,
    transfer_out  = transfer_out  + CASE WHEN NEW.component = 'TRANSFER_OUT'  THEN d ELSE 0 END,
    reservation   = reservation   + CASE WHEN NEW.component = 'RESERVATION'   THEN d ELSE 0 END,
    commitment    = commitment    + CASE WHEN NEW.component = 'COMMITMENT'    THEN d ELSE 0 END,
    actual        = actual        + CASE WHEN NEW.component = 'ACTUAL'        THEN d ELSE 0 END,
    version = version + 1,
    updated_at = now()
  WHERE budget_line_id = NEW.budget_line_id;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_le_balance AFTER INSERT ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION ledger_apply_balance();

-- توازن المناقلة عند نهاية المعاملة (INV-02)
CREATE FUNCTION ledger_check_transfer_group() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s_out NUMERIC; s_in NUMERIC; n_years INT;
BEGIN
  SELECT coalesce(sum(direction * amount) FILTER (WHERE component = 'TRANSFER_OUT'), 0),
         coalesce(sum(direction * amount) FILTER (WHERE component = 'TRANSFER_IN'), 0),
         count(DISTINCT fiscal_year_id)
    INTO s_out, s_in, n_years
    FROM ledger_entries WHERE transfer_group_id = NEW.transfer_group_id;
  IF s_out <> s_in THEN
    RAISE EXCEPTION 'مناقلة غير متوازنة: صادر % ≠ وارد %', s_out, s_in USING ERRCODE = 'P0001';
  END IF;
  IF n_years > 1 THEN
    RAISE EXCEPTION 'المناقلة يجب أن تكون داخل سنة مالية واحدة' USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_le_transfer_balance AFTER INSERT ON ledger_entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  WHEN (NEW.transfer_group_id IS NOT NULL)
  EXECUTE FUNCTION ledger_check_transfer_group();

-- مطابقة الأرصدة مع القيود (INV-08): يعيد الأسطر المختلفة
CREATE VIEW v_balance_reconciliation AS
WITH sums AS (
  SELECT budget_line_id,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'APPROPRIATION'), 0) AS appropriation,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'ALLOCATION'), 0)    AS allocation,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'TRANSFER_IN'), 0)   AS transfer_in,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'TRANSFER_OUT'), 0)  AS transfer_out,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'RESERVATION'), 0)   AS reservation,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'COMMITMENT'), 0)    AS commitment,
    coalesce(sum(direction * amount) FILTER (WHERE component = 'ACTUAL'), 0)        AS actual
  FROM ledger_entries GROUP BY budget_line_id
)
SELECT b.budget_line_id
FROM budget_balances b LEFT JOIN sums s ON s.budget_line_id = b.budget_line_id
WHERE b.appropriation <> coalesce(s.appropriation, 0) OR b.allocation <> coalesce(s.allocation, 0)
   OR b.transfer_in <> coalesce(s.transfer_in, 0) OR b.transfer_out <> coalesce(s.transfer_out, 0)
   OR b.reservation <> coalesce(s.reservation, 0) OR b.commitment <> coalesce(s.commitment, 0)
   OR b.actual <> coalesce(s.actual, 0);

-- ============================================================================
-- triggers التدقيق على الجداول
-- ============================================================================
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['users', 'roles', 'role_permissions', 'user_roles', 'user_scopes',
                           'entities', 'budget_chapters', 'budget_items', 'fiscal_years', 'fiscal_periods',
                           'budget_lines', 'funding_sources', 'suppliers', 'override_grants', 'ledger_entries']
  LOOP
    EXECUTE format('CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                   'FOR EACH ROW EXECUTE FUNCTION audit_row()', t);
  END LOOP;
END $$;

-- مستخدم النظام (للعمليات الآلية والترحيلات)
DO $$
BEGIN
  PERFORM set_config('gbcfms.user_id', '00000000-0000-0000-0000-000000000001', true);
  PERFORM set_config('gbcfms.reason', 'bootstrap', true);
  INSERT INTO users (id, username, full_name, password_hash, is_active, is_system, must_change_password)
  VALUES ('00000000-0000-0000-0000-000000000001', 'system', 'النظام', '!', FALSE, TRUE, FALSE);
END $$;
"""

DOWNGRADE = r"""
DROP VIEW IF EXISTS v_balance_reconciliation;
DROP TABLE IF EXISTS ledger_entries, override_grants, suppliers, funding_sources, budget_balances,
  budget_lines, fiscal_periods, fiscal_years, budget_items, budget_chapters, entities, refresh_tokens,
  user_scopes, user_roles, role_permissions, permissions, roles, users, audit_log CASCADE;
DROP FUNCTION IF EXISTS ledger_check_transfer_group, ledger_apply_balance, ledger_validate,
  create_balance_row, forbid_mutation, audit_verify_chain, audit_row, audit_append, audit_hash CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
