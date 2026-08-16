"""
User theme as a DB enum instead of free text.

Revision ID: 0004_theme_enum
Revises: 0003_snapshot_tables
Create Date: 2026-08-16

The base app tables are created by SQLModel's create_all (seed), where the
enum type user_theme already exists on fresh schemas. This migration brings
existing databases in line: it creates the type (checkfirst) and converts
the TEXT column, casting current values ('system'/'light'/'dark') to the
enum. Idempotent — safe on already-migrated and fresh schemas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_theme_enum"
down_revision: str | None = "0003_snapshot_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "app_user" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("app_user")}
    if "theme" not in columns:
        return

    sa.Enum("system", "light", "dark", name="user_theme").create(bind, checkfirst=True)
    # Postgres can't auto-cast the existing column default ('system' text literal)
    # during the type change — drop it first, restore it afterwards.
    op.execute("ALTER TABLE app_user ALTER COLUMN theme DROP DEFAULT")
    op.execute("ALTER TABLE app_user ALTER COLUMN theme TYPE user_theme USING theme::user_theme")
    op.alter_column("app_user", "theme", server_default=sa.text("'system'::user_theme"), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "app_user" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("app_user")}
    if "theme" not in columns:
        return
    op.alter_column(
        "app_user",
        "theme",
        type_=sa.Text(),
        postgresql_using="theme::text",
        server_default="system",
        nullable=False,
    )
    op.execute("DROP TYPE IF EXISTS user_theme")
