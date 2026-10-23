"""
Drop the renovation reference `value_key` column.

Why: `value_key` was a human-readable identity carried over from the source
workbook (`work_type_004`). Nothing references it — the plan stores the option
UUID — and options created in the UI only got a random suffix. The row id is
the identity now, exactly like every other dictionary in the project. Seeded
options keep stable ids: `reference_data.reference_option_id()` derives them
from `(catalog, label)`, so a re-seed finds its own rows.

What: drops the column and its uniqueness index; the existing option ids are
untouched, so every plan reference stays valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_drop_reference_value_key"
down_revision: str | None = "0020_alembic_version_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "r_reference_values"
_INDEX = "ix_r_reference_values_catalog_key"


def upgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "value_key")


def downgrade() -> None:
    # The original keys are not recoverable: the column comes back filled with
    # a placeholder derived from the id, which is unique on its own.
    op.add_column(_TABLE, sa.Column("value_key", sa.Text(), nullable=True))
    op.execute(f"UPDATE {_TABLE} SET value_key = id::text WHERE value_key IS NULL")  # noqa: S608 - fixed identifiers
    op.alter_column(_TABLE, "value_key", nullable=False)
    op.create_index(_INDEX, _TABLE, ["catalog", "value_key"], unique=True)
