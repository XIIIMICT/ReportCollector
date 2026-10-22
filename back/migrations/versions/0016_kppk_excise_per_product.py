"""
Make the excise a product-level value instead of a per-market cell.

The «Реализация» copies store one row per (ДЗО, период, продукт, рынок), so the
excise column was duplicated down the product's three market rows (РФ / ДЗ /
БЗ) and had to be kept in sync by hand. The excise is a property of the PRODUCT
for a slice, not of the market it is sold to — the form now edits it once.

The columns stay on the same rows (no new table, no key change): the collapse
keeps the value on every market row of the product, so a read that needs it
from any single row still sees it, while the API exposes it once per product.
Where the copies disagree, the largest non-zero value wins — excise is a tax
rate, and a stray zero/half-filled copy must not erase a real rate.

Revision ID: 0016_kppk_excise_per_product
Revises: 0015_updater_user_catalog
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0016_kppk_excise_per_product"
down_revision: str | None = "0015_updater_user_catalog"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# table -> excise columns it carries.
TABLES: dict[str, tuple[str, ...]] = {
    "kppk_realization_plan": ("excise_bp", "excise_max_plan"),
    "kppk_realization": ("excise_fact",),
}


def upgrade() -> None:
    """Collapse the per-market excise into one value per product."""
    connection = op.get_bind()
    for table, columns in TABLES.items():
        for column in columns:
            # `max()` over the product's market rows: the strongest non-zero
            # value wins, and an all-zero product stays zero.
            connection.execute(
                sa.text(
                    f"""
                    UPDATE {table} r
                    SET {column} = spread.value
                    FROM (
                        SELECT org_id, period_date, product, max({column}) AS value
                        FROM {table}
                        GROUP BY org_id, period_date, product
                    ) AS spread
                    WHERE r.org_id = spread.org_id
                      AND r.period_date = spread.period_date
                      AND r.product = spread.product
                    """  # noqa: S608 — migration-owned identifiers
                )
            )
            # A product-level figure is never NULL: the form shows an editable
            # cell for every row, and "no excise" means a real zero.
            connection.execute(
                sa.text(f"UPDATE {table} SET {column} = 0 WHERE {column} IS NULL")  # noqa: S608
            )
            op.alter_column(
                table,
                column,
                existing_type=sa.Numeric(precision=20, scale=2),
                nullable=False,
                server_default=sa.text("0"),
            )


def downgrade() -> None:
    """Return the columns to their nullable pre-collapse shape."""
    for table, columns in TABLES.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                existing_type=sa.Numeric(precision=20, scale=2),
                nullable=True,
                server_default=None,
            )
