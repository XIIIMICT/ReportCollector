"""
Add the «Заблокирован» role to `r_project_role`.

The renovation enum gets the same blocked state the shared `rm_project_role`
already has: the memberships UI edits a role and `r_user_roles.is_active` is
derived from it (blocked → false), so every access resolver keeps working.
Nothing in this transaction USES the new label — a value added by `ALTER TYPE`
cannot be referenced before the transaction commits, so no data backfill lives
here (a worker without asset grants is already equivalent to blocked).

Revision ID: 0038_renovation_blocked_role
Revises: 0037_logistics_user_assets
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_renovation_blocked_role"
down_revision: str | None = "0037_logistics_user_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS keeps the migration repeatable on a schema created by
    # `create_all` (which already carries the model's enum).
    op.execute("ALTER TYPE r_project_role ADD VALUE IF NOT EXISTS 'blocked'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value; a downgrade leaves it in place.
    pass
