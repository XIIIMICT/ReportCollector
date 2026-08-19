"""
Renovation project groundwork: rm-scoped role enum rename + renovation schema.

Revision ID: 0006_renovation_roles
Revises: 0005_plural_table_names
Create Date: 2026-08-18

- user_roles.role keeps its values, but the shared enum type is renamed
  project_role → rm_project_role (the Python ProjectRole enum is
  raw-materials-specific).
- New r_project_role enum (admin/approver/block_owner/asset_worker) and
  r_block enum (tashpaev/borodiichuk/babaeva) for the renovation project.
- r_assets — the 17 renovation assets (stable code, official repair code, name).
- r_user_assets — per-user asset grants (central roles get all assets).
- r_user_roles — renovation-specific user-project roles with the optional
  responsibility block; the shared user_roles.role column keeps the RM enum.

All operations are guarded: on a fresh DB the types/tables are created by
create_all (seed) directly under the final names and nothing to rename or
create exists; on an existing DB the enum is renamed and the tables are added.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_renovation_roles"
down_revision: str | None = "0005_plural_table_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _type_exists(bind: sa.engine.Connection, name: str) -> bool:
    # Scoped to the current schema: pg_type is cluster-wide, and a type with the
    # same name in another schema (e.g. rc_test from the test run) must not
    # suppress the creation/rename in the target schema.
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = current_schema() AND t.typname = :n
                """
            ),
            {"n": name},
        ).scalar()
        is not None
    )


def _table_exists(bind: sa.engine.Connection, name: str) -> bool:
    return name in set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()

    # The raw-materials role enum is rm-specific: rename the shared type so the
    # renovation project can own the r_project_role name.
    if _type_exists(bind, "project_role") and not _type_exists(bind, "rm_project_role"):
        op.execute(sa.text("ALTER TYPE project_role RENAME TO rm_project_role"))

    if not _type_exists(bind, "r_project_role"):
        op.execute(sa.text("CREATE TYPE r_project_role AS ENUM ('admin', 'approver', 'block_owner', 'asset_worker')"))
    if not _type_exists(bind, "r_block"):
        op.execute(sa.text("CREATE TYPE r_block AS ENUM ('tashpaev', 'borodiichuk', 'babaeva')"))

    if not _table_exists(bind, "r_assets"):
        op.create_table(
            "r_assets",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("code", sa.Text(), nullable=False),
            sa.Column("repair_code", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_r_assets_code", "r_assets", ["code"])

    if not _table_exists(bind, "r_user_assets"):
        op.create_table(
            "r_user_assets",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["r_assets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "project_id", "asset_id"),
        )

    if not _table_exists(bind, "r_user_roles"):
        op.create_table(
            "r_user_roles",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "role",
                postgresql.ENUM(
                    "admin", "approver", "block_owner", "asset_worker", name="r_project_role", create_type=False
                ),
                nullable=False,
            ),
            sa.Column(
                "block",
                postgresql.ENUM("tashpaev", "borodiichuk", "babaeva", name="r_block", create_type=False),
                nullable=True,
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.Text(), nullable=False, server_default=""),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "project_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "r_user_roles"):
        op.drop_table("r_user_roles")
    if _table_exists(bind, "r_user_assets"):
        op.drop_table("r_user_assets")
    if _table_exists(bind, "r_assets"):
        op.drop_index("ix_r_assets_code", table_name="r_assets")
        op.drop_table("r_assets")

    if _type_exists(bind, "r_block"):
        op.execute(sa.text("DROP TYPE r_block"))
    if _type_exists(bind, "r_project_role"):
        op.execute(sa.text("DROP TYPE r_project_role"))

    if _type_exists(bind, "rm_project_role") and not _type_exists(bind, "project_role"):
        op.execute(sa.text("ALTER TYPE rm_project_role RENAME TO project_role"))
