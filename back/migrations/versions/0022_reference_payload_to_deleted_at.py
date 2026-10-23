"""
Drop the unused renovation reference `payload`, add `deleted_at`.

Why: `r_reference_values.payload` was never written — neither the seed nor the
UI fill it, the API does not expose it, and the only readers were the undo
snapshots saving it back. It was a placeholder that never got a purpose.

`deleted_at` is the forward-compatible soft-delete marker: a NULL value means
"not deleted", a timestamp is the moment of deletion. Deletes are still hard,
so nothing writes it yet — the column is prepared ahead of the feature.

What: drops `payload`; adds a nullable `timestamptz` `deleted_at` with no
default and no index (nothing filters on it yet).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_reference_payload_to_deleted_at"
down_revision: str | None = "0021_drop_reference_value_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "r_reference_values"


def upgrade() -> None:
    op.drop_column(_TABLE, "payload")
    op.add_column(_TABLE, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(f"COMMENT ON COLUMN {_TABLE}.deleted_at IS 'Время удаления записи; NULL — запись не удалена'")


def downgrade() -> None:
    op.drop_column(_TABLE, "deleted_at")
    op.add_column(_TABLE, sa.Column("payload", sa.Text(), nullable=True))
    op.execute(
        f"COMMENT ON COLUMN {_TABLE}.payload IS 'Дополнительные метаданные справочного значения (текстовое представление)'"
    )
