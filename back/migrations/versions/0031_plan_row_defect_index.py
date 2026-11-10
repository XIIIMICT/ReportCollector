"""
Index the defect-statement number for the plan's facet list.

`col_023` («Дефектная ведомость/ВОР №») became TEXT in 0030, and unlike the
categorical columns it is nearly UNIQUE per row — a plan of 261k rows has ~94k
distinct statements. The facet list groups the whole plan, so a seq scan cost
~1.4 s per request and the popover asks twice (the page and the column size)
before it can sort or filter by the column.

The index leads with `plan_id` like the other facet indexes (0025), which lets
PostgreSQL answer the grouping with an index-only scan.
"""

from alembic import op

revision = "0031_plan_row_defect_index"
down_revision = "0030_asset_code_and_text_defect_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_r_plan_rows_plan_col_023 ON r_plan_rows (plan_id, col_023)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_r_plan_rows_plan_col_023")
