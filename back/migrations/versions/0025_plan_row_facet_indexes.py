"""
Lead the plan-row facet indexes with plan_id so a GROUP BY can use them.

Revision ID: 0025_plan_row_facet_indexes
Revises: 0024_kppk_product_market_refs
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_plan_row_facet_indexes"
down_revision: str | None = "0024_kppk_product_market_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Columns whose header-filter facet list is read every time a funnel opens. The
# old indexes led with the column ALONE, so a query that always filters by
# plan_id fell back to a parallel sequential scan: 285 ms per facet list on the
# 306k-row plan, against 124 ms with plan_id first.
_REBUILT = ("col_015", "col_017", "col_018", "col_019", "col_020")

# Text columns the facets are most often opened on, not indexed at all before.
_ADDED = ("col_003", "col_005", "col_006", "col_007", "col_008")


def upgrade() -> None:
    # Plain SQL with `IF [NOT] EXISTS` on both sides: the migration must survive a
    # re-run after a half-applied attempt. The names are deliberately UNQUALIFIED
    # — `search_path` is the app schema (`rc` in production, `rc_test*` in the
    # test harness), which is how every other migration resolves its objects. A
    # literal `rc.` prefix broke every fresh database whose schema is not `rc`:
    # the chain failed at this revision instead of creating the indexes.
    for column in _REBUILT:
        op.execute(f"DROP INDEX IF EXISTS ix_r_plan_rows_{column}")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_plan_{column} ON r_plan_rows (plan_id, {column})")
    for column in _ADDED:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_plan_{column} ON r_plan_rows (plan_id, {column})")


def downgrade() -> None:
    for column in _ADDED:
        op.execute(f"DROP INDEX IF EXISTS ix_r_plan_rows_plan_{column}")
    for column in _REBUILT:
        op.execute(f"DROP INDEX IF EXISTS ix_r_plan_rows_plan_{column}")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_{column} ON r_plan_rows ({column})")


__all__ = ["downgrade", "upgrade"]
