"""
Create the «Логистика» asset grants.

`l_user_assets` records which `dict.orgs` assets a «Работник на активе» may
work with — an application-owned table (the `l_` prefix) whose asset id carries
an external FOREIGN KEY like the rates table. `user_roles.is_active` stays as
the derived access bit every resolver already reads; the blocked ROLE is added
by the previous revision, and a worker without grants is treated as blocked at
access time (no data backfill needed).

`just seed` (create_all) creates the table without the external FK; migrations
own the constraint.

Revision ID: 0037_logistics_user_assets
Revises: 0036_project_roles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.config import get_settings

revision: str = "0037_logistics_user_assets"
down_revision: str | None = "0036_project_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    schema = get_settings().logistics_dict_schema
    bind = op.get_bind()
    exists = bind.execute(sa.text("SELECT to_regclass(:name) IS NOT NULL").bindparams(name=f"{schema}.orgs")).scalar()
    if not exists:
        message = (
            "Для справочника пользователей «Логистики» требуется внешняя схема "
            f"{schema}: не найдена {schema}.orgs. Загрузите справочники dict "
            "или укажите LOGISTICS_DICT_SCHEMA."
        )
        raise RuntimeError(message)
    op.create_table(
        "l_user_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="ID пользователя → app_users.id"),
        sa.Column(
            "org_id",
            sa.Uuid(),
            nullable=False,
            comment="Актив, закреплённый за работником → dict.orgs.id",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updater_user_id",
            sa.Uuid(),
            nullable=True,
            comment="ID автора последнего изменения записи → app_users.id",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updater_user_id"], ["app_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], [f"{schema}.orgs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "org_id", name="l_user_assets_user_org_key"),
        comment="Логистика → Активы, закреплённые за работником на активе",
    )
    op.create_index("ix_l_user_assets_user_id", "l_user_assets", ["user_id"])


def downgrade() -> None:
    op.drop_table("l_user_assets")
    # The role backfill is not inverted: «Заблокирован» stays blocked (the old
    # flag is still false, so access is unchanged).
