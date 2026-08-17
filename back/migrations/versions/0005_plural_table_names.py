"""
Plural table names; drop redundant app_users columns.

Revision ID: 0005_plural_table_names
Revises: 0004_theme_enum
Create Date: 2026-08-17

Base tables are renamed to the conventional plural form (app_user → app_users,
rm_material_movement → rm_material_movements, ...). The redundant `key`
column (duplicates the normalized email) and `oidc_issuer` (constant for a
single realm) are dropped from app_users. All operations are guarded: on a
fresh DB the tables are created by create_all (seed) directly under the
plural names and nothing to rename exists; on an existing DB the singular
tables are renamed in place. FKs referencing renamed tables keep working —
PostgreSQL tracks them by OID.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_plural_table_names"
down_revision: str | None = "0004_theme_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Old singular name → new plural name.
_TABLE_RENAMES: dict[str, str] = {
    "app_user": "app_users",
    "project": "projects",
    "user_role": "user_roles",
    "rm_asset": "rm_assets",
    "rm_user_asset": "rm_user_assets",
    "rm_raw_material": "rm_raw_materials",
    "rm_asset_raw_material": "rm_asset_raw_materials",
    "rm_movement_period": "rm_movement_periods",
    "rm_material_movement": "rm_material_movements",
    "rm_delivery_basis": "rm_delivery_bases",
    "rm_raw_material_price": "rm_raw_material_prices",
    "rm_report_snapshot": "rm_report_snapshots",
    "rm_snapshot_material_movement": "rm_snapshot_material_movements",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for old_name, new_name in _TABLE_RENAMES.items():
        if old_name in tables and new_name not in tables:
            op.rename_table(old_name, new_name)

    # Re-inspect after the renames: the column checks must see the plural names.
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("app_users")} if "app_users" in tables else set()
    if "key" in columns:
        op.drop_column("app_users", "key")
    if "oidc_issuer" in columns:
        op.drop_column("app_users", "oidc_issuer")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "app_users" in tables:
        columns = {column["name"] for column in inspector.get_columns("app_users")}
        if "oidc_issuer" not in columns:
            op.add_column("app_users", sa.Column("oidc_issuer", sa.Text(), nullable=True))
        if "key" not in columns:
            op.add_column("app_users", sa.Column("key", sa.Text(), nullable=True))
    for old_name, new_name in reversed(list(_TABLE_RENAMES.items())):
        if new_name in tables and old_name not in tables:
            op.rename_table(new_name, old_name)
