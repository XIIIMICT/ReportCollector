"""
Drop the renovation reference order column.

Why: `r_reference_values.sort_order` only ever reproduced the Excel row order
of the source workbook. In practice these catalogs are read as dictionaries, so
a stable alphabetical order by `label` is what users need: the stored order
made the lists look unordered and forced every new option to invent a number.

What: drops the column; the application now orders by `label` alone, and the
catalog comment loses its `sort_order` description. The KPPK dictionaries keep
their own source numbering (`product_num` / `raw_num`) and are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_drop_reference_sort_order"
down_revision: str | None = "0018_kppk_excise_on_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "r_reference_values"
_COLUMN = "sort_order"


def upgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)


def downgrade() -> None:
    # The original order is not recoverable; the column comes back zeroed.
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
    )
