"""
Make «Продуктовые позиции» a product × ДЗО row and keep the excise flag there.

The dictionary used to edit one row per PRODUCT with a multi-select «ДЗО» cell
(a comma list), and the excise switch lived on the ДЗО row. Both are wrong for
the way the form is used:

- the ДЗО is a property of ONE product position, so every (product, ДЗО) pair is
  its own row — the same product legitimately appears at several assets;
- «Заполняемый акциз» is a property of the PAIR (this product at this asset
  carries an excise), so it lives on `kppk_org_products.has_excise`, which
  0017 already created and filled. The ДЗО-level boolean becomes a DERIVED
  projection: a ДЗО "has an excise" exactly when one of its positions does.

This migration drops the ДЗО-level column (nothing should be edited at that
level any more) and leaves the per-pair flag as the single source of truth.

Revision ID: 0018_kppk_excise_on_position
Revises: 0017_kppk_excise_scope
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0018_kppk_excise_on_position"
down_revision: str | None = "0017_kppk_excise_scope"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Drop the ДЗО-level switch; the per-product position flag stays."""
    op.drop_column("kppk_orgs", "has_excise")


def downgrade() -> None:
    """Restore the ДЗО-level switch, derived from the pair flags."""
    op.add_column(
        "kppk_orgs",
        sa.Column("has_excise", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        sa.text(
            "UPDATE kppk_orgs o SET has_excise = true "
            "WHERE EXISTS (SELECT 1 FROM kppk_org_products op "
            "WHERE op.org_id = o.id AND op.has_excise)"
        )
    )
