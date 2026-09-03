"""
Allow empty (asset-less) renovation plan rows.

Empty rows are added instantly (e.g. by an admin / a block owner to draft work)
and are removed again on submit if never filled. Until then their asset_id is
NULL, so the NOT NULL constraint on renovation_plan_rows.asset_id is relaxed.

Revision ID: 0014_plan_row_asset_nullable
Revises: 0013_renovation_ref_indexes
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_plan_row_asset_nullable"
down_revision: str | None = "0013_renovation_ref_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("renovation_plan_rows", "asset_id", nullable=True)


def downgrade() -> None:
    # Empty rows cannot satisfy a restored NOT NULL — drop them first.
    op.execute(sa.text("DELETE FROM renovation_plan_rows WHERE asset_id IS NULL"))
    op.alter_column("renovation_plan_rows", "asset_id", nullable=False)
