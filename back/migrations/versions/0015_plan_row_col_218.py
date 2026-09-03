"""
Add a free-text 'correction' column to renovation plan rows.

The renovation form gained a nullable text input «Корректировка (при
отсутствии дефектной ведомости)» (field col_218), used when a work item has no
defect-введомости number (col_023). It is stored as a plain nullable TEXT
column on renovation_plan_rows and has no formula side-effects on existing rows.

Revision ID: 0015_plan_row_col_218
Revises: 0014_plan_row_asset_nullable
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_plan_row_col_218"
down_revision: str | None = "0014_plan_row_asset_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "renovation_plan_rows",
        sa.Column("col_218", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("renovation_plan_rows", "col_218")
