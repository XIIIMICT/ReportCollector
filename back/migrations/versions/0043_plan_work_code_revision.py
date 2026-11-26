"""
Plan-level work-code revision for the preflight cache.

**Updated in place before rollout — never edit a migration applied to a shared
environment.** Local read-only evidence (2026-11-27): the disposable CI stack
`rc_ci` is at `0043_plan_work_code_revision` in schema `rc`, while the
persistent local dev database still has
`rc.alembic_version = 0041_plan_rows_semantic_names`; the only other
installations of these triggers are the disposable `rc_test*` schemas rebuilt
by the test suite. No shared/persistent environment has applied 0043, so the
plan-transfer correction below is made IN PLACE instead of shipping a forward
migration. Do not execute destructive migrations against a live database.

It adds `r_plans.revision` and three statement-level triggers on `r_plan_rows`
that increment it in the SAME transaction as every write that can change a
plan's set of work codes: row inserts, row deletes and updates that change
`work_or_repair_code` OR move a row to another `plan_id`. Moving a row even
with an UNCHANGED code changes the code set of BOTH plans, so both revisions
are bumped. A set-based UPDATE (import, recompute, multi-plan move) bumps once
per affected plan per statement, not once per row. The trigger lives in the
database, so cell edits, row lifecycle, undo/redo, imports, recompute and any
script/maintenance write are all covered without an app-level call site.

Preflight reads the revision with the working plan's id; the versioned cache
key changes whenever it changes. A change in an archived plan bumps only THAT
plan's counter and cannot invalidate a report for the working plan.

Write-path audit (RC-1): no current application or maintenance path UPDATEs
`plan_id` on an existing row. Cell edits set only form fields (`plan_id` is not
one); row create/undo recreate and the lifecycle copy are INSERTs; row delete
and its undo are DELETEs; import apply, recompute and catalog replacement
update only value columns. The transfer case is still covered defensively:
the DB is a shared boundary (psql, future code) and must stay correct even
when the moved row's code is unchanged.

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
# costs one plan-row increment per plan, not 20k increments. `id IN (...)`
# updates each plan row once even when several source rows name the same plan;
# UNION makes that dedup explicit. `IS DISTINCT FROM` handles nullable codes and
# keeps an unrelated UPDATE (no code/plan change) from bumping anything.
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
        -- A moved row removes a code from its old plan and adds it to the new
        -- one even when work_or_repair_code itself is unchanged. A pure code
        -- edit bumps the owning plan; both SELECTs return the same id and
        -- UNION collapses them to a single increment.
        UPDATE r_plans SET revision = revision + 1
        WHERE id IN (
            SELECT o.plan_id
            FROM old_rows AS o
            JOIN new_rows AS n ON n.id = o.id
            WHERE o.plan_id IS DISTINCT FROM n.plan_id
               OR o.work_or_repair_code IS DISTINCT FROM n.work_or_repair_code
            UNION
            SELECT n.plan_id
            FROM old_rows AS o
            JOIN new_rows AS n ON n.id = o.id
            WHERE o.plan_id IS DISTINCT FROM n.plan_id
               OR o.work_or_repair_code IS DISTINCT FROM n.work_or_repair_code
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
