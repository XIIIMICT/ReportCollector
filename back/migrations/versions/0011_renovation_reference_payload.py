"""
Reference value payload column on the renovation reference catalogs.

The RenovationReferenceValue model (plan.py) carries an optional text `payload`
(used for richer reference options). The table existed on prod without this
column — 0007/0008 never added it, so seed-renovation (which writes payloads)
failed with «column renovation_reference_values.payload does not exist». Fresh
schemas get the column from create_all; existing schemas get it here, guarded
the same way as 0007/0008.

Revision ID: 0011_renovation_reference_payload
Revises: 0010_move_storage_to_prices
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_renovation_reference_payload"
down_revision: str | None = "0010_move_storage_to_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(bind: sa.engine.Connection, table: str, column: str) -> bool:
    if table not in set(sa.inspect(bind).get_table_names()):
        return False
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "renovation_reference_values", "payload"):
        op.add_column(
            "renovation_reference_values",
            sa.Column("payload", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "renovation_reference_values", "payload"):
        op.drop_column("renovation_reference_values", "payload")
