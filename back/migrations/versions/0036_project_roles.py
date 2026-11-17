"""
Add the «Ревьюер» and «Заблокирован» roles to `rm_project_role`.

The enum is shared by every project, so the values are appended globally; each
project's UI decides which of them it offers. «Заблокирован» replaces the old
«доступ к проекту» flag as the user-facing state — `user_roles.is_active` is
kept and derived from the role (blocked = false), so every access resolver
keeps working unchanged.

A new enum value cannot be USED in the transaction that adds it, so no
migration in the same chain writes these labels: a membership that keeps the
`asset_worker` role but has no asset grants is already equivalent to blocked
(the access resolver enforces it).

Revision ID: 0036_project_roles
Revises: 0035_logistics_rates
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_project_roles"
down_revision: str | None = "0035_logistics_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("reviewer", "blocked")


def upgrade() -> None:
    for role in _ROLES:
        # IF NOT EXISTS keeps the migration repeatable on a schema created by
        # `create_all` (which already carries the model's enum). Nothing in the
        # chain USES the new labels inside this transaction: a membership that
        # stays `asset_worker` without grants is already treated as blocked, so
        # there is no data backfill here.
        op.execute(f"ALTER TYPE rm_project_role ADD VALUE IF NOT EXISTS '{role}'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value; a downgrade leaves the values in
    # place (they are unused once the code stops writing them).
    pass
