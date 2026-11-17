"""
Create and seed the «Класс опасности» dictionary (values 1..9).

Why a local table, not an external one: the nine hazard classes are fixed
application reference data, unlike the `dict` schema that belongs to the
corporate logistics database and is never written here. The table is not shown
as a tab yet; the future main table will reference it.

Revision ID: 0033_logistics_hazard_classes
Revises: 0032_logistics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_logistics_hazard_classes"
down_revision: str | None = "0032_logistics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "logistics_hazard_classes",
        sa.Column("value", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("value"),
        comment="Логистика → Справочник классов опасности от 1 до 9",
    )
    op.execute("COMMENT ON COLUMN logistics_hazard_classes.value IS 'Класс опасности от 1 до 9'")
    op.execute("COMMENT ON COLUMN logistics_hazard_classes.name IS 'Наименование класса опасности'")
    op.bulk_insert(
        sa.table(
            "logistics_hazard_classes",
            sa.column("value", sa.Integer),
            sa.column("name", sa.Text),
        ),
        [{"value": value, "name": f"{value} класс"} for value in range(1, 10)],
    )


def downgrade() -> None:
    op.drop_table("logistics_hazard_classes")
