"""
Move storage_standard_tons from movements to prices.

Revision ID: 0010_move_storage_to_prices
Revises: 0009_renovation_asset_unique
Create Date: 2026-08-26

The storage standard is a property of the raw-material item, not a
per-month movement value.  It now lives in rm_raw_material_prices
alongside the business-plan fields, and is projected into the movement
form as a read-only column (same pattern as businessPlanTons).

Data loss for this column is acceptable at this stage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_move_storage_to_prices"
down_revision: str | None = "0009_renovation_asset_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add columns to the prices table.
    op.add_column(
        "rm_raw_material_prices",
        sa.Column("storage_standard_tons", sa.Float(), nullable=True),
    )
    op.add_column(
        "rm_raw_material_prices",
        sa.Column("storage_standard_updated_at", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "rm_raw_material_prices",
        sa.Column("storage_standard_updated_by", sa.Text(), server_default="", nullable=False),
    )

    # 2. Copy the latest non-null value from movements into prices.
    #    Movements are per-month; prices are per ARM (one row).
    op.execute(
        """
        UPDATE rm_raw_material_prices p
        SET storage_standard_tons = sub.storage_standard_tons
        FROM (
            SELECT DISTINCT ON (m.asset_raw_material_id)
                   m.asset_raw_material_id,
                   m.storage_standard_tons
            FROM rm_material_movements m
            WHERE m.storage_standard_tons IS NOT NULL
            ORDER BY m.asset_raw_material_id, m.report_month DESC
        ) sub
        WHERE p.asset_raw_material_id = sub.asset_raw_material_id
        """
    )

    # 3. Drop the column from the movements table.
    op.drop_column("rm_material_movements", "storage_standard_tons")


def downgrade() -> None:
    # Reverse: add column back to movements, copy, drop from prices.
    op.add_column(
        "rm_material_movements",
        sa.Column("storage_standard_tons", sa.Float(), nullable=True),
    )
    op.execute(
        """
        UPDATE rm_material_movements m
        SET storage_standard_tons = p.storage_standard_tons
        FROM rm_raw_material_prices p
        WHERE m.asset_raw_material_id = p.asset_raw_material_id
          AND p.storage_standard_tons IS NOT NULL
        """
    )
    op.drop_column("rm_raw_material_prices", "storage_standard_updated_by")
    op.drop_column("rm_raw_material_prices", "storage_standard_updated_at")
    op.drop_column("rm_raw_material_prices", "storage_standard_tons")
