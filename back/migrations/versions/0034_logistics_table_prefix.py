"""
Rename the logistics dictionary table to the project's `l_` prefix.

The «Логистика» tables are prefixed `l_`; 0033 created the first one before the
convention was fixed. The rename keeps the rows, the primary key and the
comments (PostgreSQL keeps them on the renamed relation).

Revision ID: 0034_logistics_table_prefix
Revises: 0033_logistics_hazard_classes
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034_logistics_table_prefix"
down_revision: str | None = "0033_logistics_hazard_classes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("logistics_hazard_classes", "l_hazard_classes")


def downgrade() -> None:
    op.rename_table("l_hazard_classes", "logistics_hazard_classes")
