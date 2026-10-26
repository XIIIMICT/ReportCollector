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


def _schema() -> str:
    """Return the schema the migration runs in (the app is not tied to `public`)."""
    return op.get_context().version_table_schema or "rc"


def upgrade() -> None:
    schema = _schema()
    # Plain SQL with `IF [NOT] EXISTS` on both sides: the migration must survive a
    # re-run after a half-applied attempt, and the test harness creates the table
    # from the ORM metadata (where these indexes do not exist yet).
    for column in _REBUILT:
        op.execute(f"DROP INDEX IF EXISTS {schema}.ix_r_plan_rows_{column}")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_plan_{column} ON {schema}.r_plan_rows (plan_id, {column})"
        )
    for column in _ADDED:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_plan_{column} ON {schema}.r_plan_rows (plan_id, {column})"
        )


def downgrade() -> None:
    schema = _schema()
    for column in _ADDED:
        op.execute(f"DROP INDEX IF EXISTS {schema}.ix_r_plan_rows_plan_{column}")
    for column in _REBUILT:
        op.execute(f"DROP INDEX IF EXISTS {schema}.ix_r_plan_rows_plan_{column}")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_r_plan_rows_{column} ON {schema}.r_plan_rows ({column})")


__all__ = ["downgrade", "upgrade"]
