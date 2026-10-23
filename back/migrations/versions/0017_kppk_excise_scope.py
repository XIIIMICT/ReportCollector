"""
Scope the excise to the assets (and products) that actually carry one.

The excise is not a property of every product on every asset. Two switches are
needed, and they live in two different places:

- `kppk_orgs.has_excise` — «Заполняемый акциз»: whether the ДЗО levies an excise
  at all. It is part of the ДЗО dictionary (editable in the «ДЗО» tab) and is the
  on/off switch of the «Акциз» column in «Реализация».
- `kppk_org_products.has_excise` — which products of that asset carry it. A
  product × ДЗО pair, because the same product may carry an excise at one asset
  and none at another.

The migration seeds today's known scope (the three products of
«Минудобрения»); names are matched with quotes and spacing normalised away (the
source writes `АО "…"` where a hand-kept list writes `АО «…»`).

Revision ID: 0017_kppk_excise_scope
Revises: 0016_kppk_excise_per_product
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

from src.common.data_catalog import SPECIFIC_COLUMNS

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0017_kppk_excise_scope"
down_revision: str | None = "0016_kppk_excise_per_product"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# asset name -> product names that carry an excise there.
INITIAL_EXCISE_SCOPE: dict[str, tuple[str, ...]] = {
    "АО «Минудобрения»": ("Аммиак", "Аммиачная селитра", "Фосфорные удобрения"),
}

# «АО "X"» and «АО «X»» must compare equal: quotes and doubled spaces folded.
_NORM = "lower(replace(replace(replace(trim({value}), '«', '\"'), '»', '\"'), '  ', ' '))"


def upgrade() -> None:
    """Add both flags and seed the known product × ДЗО combinations."""
    connection = op.get_bind()
    op.add_column(
        "kppk_orgs",
        sa.Column("has_excise", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "kppk_org_products",
        sa.Column("has_excise", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # The catalog (`src/common/data_catalog.py`) owns the descriptions and the
    # schema-drift check compares them, so the comment is taken FROM there — one
    # source of truth, no hand-copied text to drift. Only the pair flag gets one:
    # the ДЗО-level column from this migration is dropped again by the NEXT one
    # (0018), where «Заполняемый акциз» moves to the product position.
    description = SPECIFIC_COLUMNS["kppk_org_products"]["has_excise"].rstrip(".")
    # A quoted string literal, not a bound parameter: `COMMENT ON` accepts no
    # placeholders. A doubled quote makes it a valid SQL string.
    literal = description.replace("'", "''")
    connection.execute(sa.text(f"COMMENT ON COLUMN kppk_org_products.has_excise IS '{literal}'"))
    for org_name, products in INITIAL_EXCISE_SCOPE.items():
        connection.execute(
            sa.text(
                f"UPDATE kppk_orgs SET has_excise = true "  # noqa: S608 — migration-owned fragment
                f"WHERE {_NORM.format(value='name')} = {_NORM.format(value=':org_name')}"
            ),
            {"org_name": org_name},
        )
        for product in products:
            connection.execute(
                sa.text(
                    f"UPDATE kppk_org_products op SET has_excise = true "  # noqa: S608 — migration-owned fragment
                    f"FROM kppk_orgs o, kppk_products p "
                    f"WHERE o.id = op.org_id AND p.id = op.product_id "
                    f"AND {_NORM.format(value='o.name')} = {_NORM.format(value=':org_name')} "
                    f"AND lower(trim(p.name)) = lower(trim(:product))"
                ),
                {"org_name": org_name, "product": product},
            )


def downgrade() -> None:
    """Drop both flags."""
    op.drop_column("kppk_org_products", "has_excise")
    op.drop_column("kppk_orgs", "has_excise")
