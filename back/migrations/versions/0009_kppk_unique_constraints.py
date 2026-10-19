"""
Add the uniqueness guarantees of the «КППК» dictionaries.

The project matches dictionary rows by their names and source numbers (the
seeder, the grids, and the upcoming «Таблицы.xlsx» phase), so both carry UNIQUE
constraints now: the source numbers (`kppk_products.product_num`,
`kppk_raws.raw_num`) and the display names (`kppk_raws.name`, `kppk_periods` by
month + year). Several NULL numbers stay allowed — PostgreSQL treats NULLs as
distinct, and the matrix brings in products without a number.

`kppk_orgs` (`org_id`/`code`/`name`), `kppk_products.name` and
`kppk_periods.period_id` were already unique since 0008.

Revision ID: 0009_kppk_unique_constraints
Revises: 0008_kppk_source_tables
Create Date: 2026-10-17 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_kppk_unique_constraints"
down_revision: str | None = "0008_kppk_source_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("kppk_products_product_num_key", "kppk_products", ["product_num"])
    op.create_unique_constraint("kppk_raws_name_key", "kppk_raws", ["name"])
    op.create_unique_constraint("kppk_raws_raw_num_key", "kppk_raws", ["raw_num"])
    op.create_unique_constraint("uq_kppk_periods_month_year", "kppk_periods", ["month_name", "year_num"])


def downgrade() -> None:
    op.drop_constraint("uq_kppk_periods_month_year", "kppk_periods", type_="unique")
    op.drop_constraint("kppk_raws_raw_num_key", "kppk_raws", type_="unique")
    op.drop_constraint("kppk_raws_name_key", "kppk_raws", type_="unique")
    op.drop_constraint("kppk_products_product_num_key", "kppk_products", type_="unique")
