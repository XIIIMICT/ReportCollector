"""
Unique option label per renovation reference catalog.

The «Виды работ»-style catalogs are dictionaries: one entry per label within
a catalog. Previously nothing enforced it, so an accidental double add
produced two rows with the same label. The migration removes existing
duplicates (keeping the earliest row per label, case-insensitively) and adds
a unique index on (catalog, lower(label)). If a duplicate is still referenced
by plan rows, the FK aborts the delete loudly — resolve those manually first.

Fresh schemas already have the index via create_all (SQLModel metadata); the
index add is guarded by existence, like 0009.

Revision ID: 0012_option_label_unique
Revises: 0011_renovation_ref_payload
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_option_label_unique"
down_revision: str | None = "0011_renovation_ref_payload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_renovation_reference_values_catalog_label"


def _index_exists(bind: sa.engine.Connection, table: str, name: str) -> bool:
    if table not in set(sa.inspect(bind).get_table_names()):
        return False
    return any(index["name"] == name for index in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    # Drop duplicates, keeping the earliest entry per (catalog, label).
    op.execute(
        """
        DELETE FROM renovation_reference_values a
        USING renovation_reference_values b
        WHERE a.catalog = b.catalog
          AND lower(a.label) = lower(b.label)
          AND (b.sort_order, b.id) < (a.sort_order, a.id)
        """
    )
    bind = op.get_bind()
    if not _index_exists(bind, "renovation_reference_values", INDEX_NAME):
        op.create_index(
            INDEX_NAME,
            "renovation_reference_values",
            ["catalog", sa.text("lower(label)")],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _index_exists(bind, "renovation_reference_values", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="renovation_reference_values")
