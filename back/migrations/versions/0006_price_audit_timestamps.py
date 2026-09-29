"""
Store the «Цены» per-field audit timestamps as timestamptz.

`business_plan_updated_at` and `storage_standard_updated_at` were TEXT columns
holding ISO strings (empty when never set), which forced `movement_audit` to
re-parse every value per row in Python and allowed non-datetime junk into the
column. They are real `timestamptz` columns now; the empty legacy values
become NULL (AUD-22).
"""

from alembic import op

revision = "0006_price_audit_timestamps"
down_revision = "0005_user_role_project_indexes"
branch_labels = None
depends_on = None

_COLUMNS = ("business_plan_updated_at", "storage_standard_updated_at")

# A legacy installation could carry junk in the TEXT column (the old desktop
# app wrote a '<УГАДАЙТЕ>' stub); an unparseable value becomes NULL rather than
# aborting the migration. `pg_input_is_valid` would be the natural guard but it
# only exists from PostgreSQL 16, and production still runs 14, so the cast goes
# through a throwaway plpgsql wrapper that swallows data errors (and only data
# errors — a statement timeout or cancellation must still abort).
_SAFE_CAST_FUNCTION = "rc_migration_0006_safe_timestamptz"
_SAFE_CAST_DDL = f"""
CREATE OR REPLACE FUNCTION {_SAFE_CAST_FUNCTION}(value text) RETURNS timestamptz
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN value::timestamptz;
EXCEPTION WHEN data_exception THEN
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.execute(_SAFE_CAST_DDL)
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE rm_raw_material_prices "
            f"ALTER COLUMN {column} DROP DEFAULT, "
            f"ALTER COLUMN {column} DROP NOT NULL, "
            f"ALTER COLUMN {column} TYPE timestamptz USING {_SAFE_CAST_FUNCTION}({column})"
        )
    op.execute(f"DROP FUNCTION {_SAFE_CAST_FUNCTION}(text)")


def downgrade() -> None:
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE rm_raw_material_prices "
            f"ALTER COLUMN {column} TYPE text USING COALESCE({column}::text, ''), "
            f"ALTER COLUMN {column} SET DEFAULT '', "
            f"ALTER COLUMN {column} SET NOT NULL"
        )
