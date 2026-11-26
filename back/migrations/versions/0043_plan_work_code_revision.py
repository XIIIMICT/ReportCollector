"""
Plan-level work-code revision for the preflight cache.

**Author only — the author did not execute this migration against dev/prod.**
It adds `r_plans.revision` and three statement-level triggers on
`r_plan_rows` that increment it in the SAME transaction as every write that can
change the plan's set of work codes: row inserts, row deletes and updates that
change `work_or_repair_code`. A set-based UPDATE (import, recompute) bumps once
per affected plan per statement, not once per row. The trigger lives in the
database, so cell edits, row lifecycle, undo/redo, imports, recompute and any
script/maintenance write are all covered without an app-level call site.

Preflight reads the revision with the working plan's id; the versioned cache
key changes whenever it changes. A change in an archived plan bumps only THAT
plan's counter and cannot invalidate a working-plan report.

Rollout note: this revision sits after `0042_plan_work_code_unique`, whose own
rollout is still blocked on live duplicate cleanup (docs/DATA-JOBS.md). Applying
`head` on a live DB therefore still requires the 0042 maintainer gate first;
the tests apply the full chain to a disposable `rc_test*` schema.

Revision ID: 0043_plan_work_code_revision
Revises: 0042_plan_work_code_unique
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_plan_work_code_revision"
down_revision: str | None = "0042_plan_work_code_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "r_plans_bump_revision"
_TRIGGERS = (
    ("r_plan_rows_revision_insert", "INSERT"),
    ("r_plan_rows_revision_delete", "DELETE"),
    ("r_plan_rows_revision_update", "UPDATE"),
)

# Unqualified names: search_path is the app schema (`rc` / `rc_test*`), as in
# 0042. Transition tables are read once per statement, so a 20k-row import UPDATE
# costs one plan-row increment per plan, not 20k increments.
_BUMP = """
CREATE OR REPLACE FUNCTION r_plans_bump_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE r_plans SET revision = revision + 1
        WHERE id IN (SELECT DISTINCT plan_id FROM new_rows);
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE r_plans SET revision = revision + 1
        WHERE id IN (SELECT DISTINCT plan_id FROM old_rows);
    ELSE
        UPDATE r_plans SET revision = revision + 1
        WHERE id IN (
            SELECT DISTINCT n.plan_id
            FROM new_rows AS n
            JOIN old_rows AS o ON o.id = n.id
            WHERE o.work_or_repair_code IS DISTINCT FROM n.work_or_repair_code
        );
    END IF;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    op.execute('ALTER TABLE "r_plans" ADD COLUMN "revision" INTEGER NOT NULL DEFAULT 0')
    # Same frozen data-catalog description the model attaches (COMMON_COLUMNS):
    # autogenerate compares comments and would otherwise report permanent drift.
    op.execute("COMMENT ON COLUMN r_plans.revision IS 'Ревизия строки; увеличивается при сохранении изменений'")
    op.execute(_BUMP)
    op.execute(
        "CREATE TRIGGER r_plan_rows_revision_insert "
        "AFTER INSERT ON r_plan_rows REFERENCING NEW TABLE AS new_rows "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER r_plan_rows_revision_delete "
        "AFTER DELETE ON r_plan_rows REFERENCING OLD TABLE AS old_rows "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER r_plan_rows_revision_update "
        "AFTER UPDATE ON r_plan_rows "
        "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION}()"
    )


def downgrade() -> None:
    for name, _event in _TRIGGERS:
        op.execute(f'DROP TRIGGER IF EXISTS "{name}" ON "r_plan_rows"')
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
    op.execute('ALTER TABLE "r_plans" DROP COLUMN IF EXISTS "revision"')


__all__ = ["downgrade", "upgrade"]
