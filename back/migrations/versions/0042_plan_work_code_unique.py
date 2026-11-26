"""
Enforce one work/repair code per renovation plan version.

**ROLLOUT BLOCKED — DO NOT APPLY TO A LIVE DATABASE YET.** The live
`r_plan_rows` still contains duplicate `work_or_repair_code` values inside one
plan; a plain `CREATE UNIQUE INDEX` fails on them by design (there is no
`IF NOT EXISTS` and no data cleanup here). Prerequisites, in order:

1. Back up the table (see docs/DATA-JOBS.md, «Work-code uniqueness rollout»).
2. Run the read-only audit
   `uv run --directory back -m src.scripts.audit_plan_work_code_duplicates`
   and decide the surviving row for every reported (plan_id, code).
3. Clean the live duplicates as a reviewed data change, then re-run the audit
   until it reports none.
4. Get maintainer approval for a maintenance window; the migration connection
   sets `lock_timeout=30s`, so a blocked build fails loudly instead of queuing.

Only then run `just migrate`. NULL codes are not constrained (PostgreSQL treats
NULLs as distinct), so empty draft rows are fine. This file is authored on the
fix branch; nothing here has been executed against dev/prod by its author.

Revision ID: 0042_plan_work_code_unique
Revises: 0041_plan_rows_semantic_names
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_plan_work_code_unique"
down_revision: str | None = "0041_plan_rows_semantic_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The semantic column from migration 0041 (the old `col_003` no longer exists).
# Unqualified name: search_path is the app schema (`rc` / `rc_test*`), as in 0025.
_INDEX = "uq_r_plan_rows_plan_work_or_repair_code"


def upgrade() -> None:
    op.execute(f'CREATE UNIQUE INDEX "{_INDEX}" ON "r_plan_rows" ("plan_id", "work_or_repair_code")')


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS "{_INDEX}"')


__all__ = ["downgrade", "upgrade"]
