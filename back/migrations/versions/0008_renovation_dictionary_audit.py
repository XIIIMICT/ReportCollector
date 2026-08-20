"""
Dictionary audit columns on the renovation dictionaries.

updated_at/updated_by on r_assets and created_at/updated_at/updated_by on
renovation_reference_values. The editable renovation dictionaries (assets and
the five reference catalogs) expose audit fields in the grid, the same way
the raw-materials dictionaries do. Fresh schemas get the columns from
create_all (SQLModel metadata), existing schemas get them here; the adds are
guarded the same way as 0007.

Revision ID: 0008_renovation_dictionary_audit
Revises: 0007_renovation_plans
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_renovation_dictionary_audit"
down_revision: str | None = "0007_renovation_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(bind: sa.engine.Connection, table: str, column: str) -> bool:
    if table not in set(sa.inspect(bind).get_table_names()):
        return False
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing(
        "r_assets",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    _add_column_if_missing(
        "r_assets",
        sa.Column("updated_by", sa.Text(), nullable=False, server_default=""),
    )
    _add_column_if_missing(
        "renovation_reference_values",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    _add_column_if_missing(
        "renovation_reference_values",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    _add_column_if_missing(
        "renovation_reference_values",
        sa.Column("updated_by", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "renovation_reference_values", "updated_by"):
        op.drop_column("renovation_reference_values", "updated_by")
    if _column_exists(bind, "renovation_reference_values", "updated_at"):
        op.drop_column("renovation_reference_values", "updated_at")
    if _column_exists(bind, "renovation_reference_values", "created_at"):
        op.drop_column("renovation_reference_values", "created_at")
    if _column_exists(bind, "r_assets", "updated_by"):
        op.drop_column("r_assets", "updated_by")
    if _column_exists(bind, "r_assets", "updated_at"):
        op.drop_column("r_assets", "updated_at")
