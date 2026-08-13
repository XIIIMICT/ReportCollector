"""
Снимки фиксации формы «Движение».

Revision ID: 0003_snapshot_tables
Revises: 0002_project_key
Create Date: 2026-08-13

Создаёт rm_report_snapshot (зафиксированные версии за месяц) и
rm_snapshot_material_movement (иммутабельные копии строк). Операция
идемпотентна: при наличии таблиц повторный запуск ничего не меняет.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_snapshot_tables"
down_revision: str | None = "0002_project_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "rm_report_snapshot" not in tables:
        op.create_table(
            "rm_report_snapshot",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("report_month", sa.Date(), nullable=False),
            sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fixed_by_user_id", sa.Uuid(), nullable=False),
            sa.Column("fixed_by_email", sa.Text(), nullable=False),
            sa.Column("fixed_by_fullname", sa.Text(), nullable=False),
            sa.Column("is_final", sa.Boolean(), nullable=False),
            sa.Column("previous_snapshot_id", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["fixed_by_user_id"], ["app_user.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("report_month"),
        )
        op.create_index("ix_rm_report_snapshot_report_month", "rm_report_snapshot", ["report_month"])
        op.create_index("ix_rm_report_snapshot_fixed_at", "rm_report_snapshot", ["fixed_at"])
        op.create_index("ix_rm_report_snapshot_previous_snapshot_id", "rm_report_snapshot", ["previous_snapshot_id"])

    if "rm_snapshot_material_movement" not in tables:
        op.create_table(
            "rm_snapshot_material_movement",
            sa.Column("snapshot_id", sa.Uuid(), nullable=False),
            sa.Column("source_asset_raw_material_id", sa.Uuid(), nullable=False),
            sa.Column("source_row_id", sa.Uuid(), nullable=False),
            sa.Column("source_asset_id", sa.Uuid(), nullable=False),
            sa.Column("source_raw_material_id", sa.Uuid(), nullable=False),
            sa.Column("asset_name", sa.Text(), nullable=False),
            sa.Column("raw_material_name", sa.Text(), nullable=False),
            sa.Column("business_plan_tons", sa.Float(), nullable=True),
            sa.Column("demand_plan_tons", sa.Float(), nullable=True),
            sa.Column("consumption_fact_tons", sa.Float(), nullable=True),
            sa.Column("demand_deviation_tons", sa.Float(), nullable=True),
            sa.Column("delivery_plan_tons", sa.Float(), nullable=True),
            sa.Column("delivery_fact_tons", sa.Float(), nullable=True),
            sa.Column("delivery_deviation_tons", sa.Float(), nullable=True),
            sa.Column("balance_start_tons", sa.Float(), nullable=True),
            sa.Column("balance_on_site_tons", sa.Float(), nullable=True),
            sa.Column("balance_in_transit_tons", sa.Float(), nullable=True),
            sa.Column("balance_end_tons", sa.Float(), nullable=True),
            sa.Column("storage_standard_tons", sa.Float(), nullable=True),
            sa.Column("deviation_reason", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["snapshot_id"], ["rm_report_snapshot.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("snapshot_id", "source_asset_raw_material_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "rm_snapshot_material_movement" in tables:
        op.drop_table("rm_snapshot_material_movement")
    if "rm_report_snapshot" in tables:
        op.drop_table("rm_report_snapshot")
