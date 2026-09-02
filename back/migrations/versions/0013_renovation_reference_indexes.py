"""
Indexes for renovation plan reference lookups.

Revision ID: 0013_renovation_ref_indexes
Revises: 0012_option_label_unique
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_renovation_ref_indexes"
down_revision: str | None = "0012_option_label_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE_COLUMNS = ("col_015", "col_017", "col_018", "col_019", "col_020")


def upgrade() -> None:
    for column in REFERENCE_COLUMNS:
        op.create_index(
            f"ix_renovation_plan_rows_{column}",
            "renovation_plan_rows",
            [column],
        )


def downgrade() -> None:
    for column in reversed(REFERENCE_COLUMNS):
        op.drop_index(f"ix_renovation_plan_rows_{column}", table_name="renovation_plan_rows")
