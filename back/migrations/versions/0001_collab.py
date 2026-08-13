"""
Совместное редактирование: состояние ячеек и журнал правок.

Revision ID: 0001_collab
Revises:
Create Date: 2026-08-13

Миграция создаёт только новые объекты (rm_cell_state, rm_edit_log) и индексы,
которых не было в схеме первой версии. Все операции идемпотентны
(if_not_exists) — безопасно для уже наполненной прод-БД и для свежих схем.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_collab"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rm_cell_state",
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_edit_action_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("canonical_key"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_rm_cell_state_row_id",
        "rm_cell_state",
        ["row_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rm_cell_state_last_user_id",
        "rm_cell_state",
        ["last_user_id"],
        unique=False,
        if_not_exists=True,
    )

    op.create_table(
        "rm_edit_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False, start=1, increment=1),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("report_month", sa.Date(), nullable=True),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_rm_edit_log_user_created",
        "rm_edit_log",
        ["user_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rm_edit_log_user_undone",
        "rm_edit_log",
        ["user_id", "undone_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rm_edit_log_row_id",
        "rm_edit_log",
        ["row_id"],
        unique=False,
        if_not_exists=True,
    )

    # Индексы существующих таблиц, которых не хватало первой версии схемы.
    # На свежей БД таблиц ещё нет — индексы создаст create_all из моделей.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "rm_material_movement" in existing_tables:
        op.create_index(
            "ix_rm_material_movement_report_month",
            "rm_material_movement",
            ["report_month"],
            unique=False,
            if_not_exists=True,
        )
    if "rm_raw_material_price" in existing_tables:
        op.create_index(
            "ix_rm_raw_material_price_delivery_basis_id",
            "rm_raw_material_price",
            ["delivery_basis_id"],
            unique=False,
            if_not_exists=True,
        )
    if "rm_asset_raw_material" in existing_tables:
        op.create_index(
            "ix_rm_asset_raw_material_raw_material_id",
            "rm_asset_raw_material",
            ["raw_material_id"],
            unique=False,
            if_not_exists=True,
        )
    if "rm_user_asset" in existing_tables:
        op.create_index(
            "ix_rm_user_asset_asset_id",
            "rm_user_asset",
            ["asset_id"],
            unique=False,
            if_not_exists=True,
        )


def downgrade() -> None:
    for index in (
        "ix_rm_user_asset_asset_id",
        "ix_rm_asset_raw_material_raw_material_id",
        "ix_rm_raw_material_price_delivery_basis_id",
        "ix_rm_material_movement_report_month",
        "ix_rm_edit_log_row_id",
        "ix_rm_edit_log_user_undone",
        "ix_rm_edit_log_user_created",
        "ix_rm_cell_state_last_user_id",
        "ix_rm_cell_state_row_id",
    ):
        op.drop_index(index, table_name=None, if_exists=True)
    op.drop_table("rm_edit_log", if_exists=True)
    op.drop_table("rm_cell_state", if_exists=True)
