"""
Make «Объем» and «Акциз» nullable in the КППК realization copies.

Why: those columns were created `NOT NULL DEFAULT 0` while every other figure
of the copies — and the source dumps themselves (`raw.kppk_f_realization*`,
`ddl.sql`: `numeric NULL`) — is nullable. The default was a mistake with real
consequences:

- an empty cell could not be stored: clearing one wrote a 0, so the form showed
  a measured "0" nobody entered, and "not filled" became indistinguishable from
  "zero" in the grid and in the Excel export;
- every re-seed refilled NULLs of the source with 0 (`coalesce(…, 0)`), which
  invented figures across whole slices;
- the API could not report "empty" at all: `exciseBp`/`exciseMaxPlan`/
  `exciseFact` were declared `float = 0`, so a NULL row read back as 0.

What: drops `NOT NULL` and the `0` server default on the nine columns —
`kppk_realization`: `buyout_fact`, `fact`, `excise_fact`;
`kppk_realization_plan`: `buyout_bp_plan`, `buyout_max_plan`, `month_bp_plan`,
`month_max_plan`, `excise_bp`, `excise_max_plan`. Existing zeros are KEPT as
zeros: they are values already stored, and guessing which of them were "never
filled" would silently rewrite data. A user who wants an empty cell clears it
once; from then on it stays empty.

Column comments are NOT touched here — `src/common/data_catalog.py` owns them
(the schema is built from the models, and the comments travel with the model
metadata), so repeating them in the migration would only make the test that
compares the migrated schema with the models fail.

Revision ID: 0023_kppk_nullable_volume_excise
Revises: 0022_reference_payload_to_deleted_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_kppk_nullable_volume_excise"
down_revision: str | None = "0022_reference_payload_to_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table → columns that lose NOT NULL and the 0 default.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "kppk_realization": ("buyout_fact", "fact", "excise_fact"),
    "kppk_realization_plan": (
        "buyout_bp_plan",
        "buyout_max_plan",
        "month_bp_plan",
        "month_max_plan",
        "excise_bp",
        "excise_max_plan",
    ),
}


def upgrade() -> None:
    """Drop NOT NULL and the 0 default; keep the stored zeros as zeros."""
    for table, columns in _COLUMNS.items():
        for column in columns:
            op.alter_column(table, column, existing_type=_numeric(column), nullable=True, server_default=None)


def downgrade() -> None:
    """Restore the 0 default; NULLs become 0 first, since the column is NOT NULL."""
    for table, columns in _COLUMNS.items():
        for column in columns:
            op.execute(f"UPDATE {table} SET {column} = 0 WHERE {column} IS NULL")  # noqa: S608 — constant names
            op.alter_column(table, column, existing_type=_numeric(column), nullable=False, server_default="0")


def _numeric(column: str) -> sa.Numeric:
    """Volumes carry 3 decimals; excise is a rate with 2."""
    return sa.Numeric(20, 3 if column.startswith(("buyout", "fact", "month")) else 2)
