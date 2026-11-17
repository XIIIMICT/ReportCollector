"""
Create the «Логистика» project row and its price-kind enum.

Why: the project must exist on an installation that only runs `just migrate`
(the dev seeder recreates everything, production does not). The enum is created
now, before the main table that will use it, so the type is owned by a single
migration instead of being introduced later by a table migration.

The external `dict` schema is NOT touched here: its tables belong to the
corporate logistics database, they are read-only for this application, and
`just migrate` must work on an installation where that schema does not exist.

Revision ID: 0032_logistics
Revises: 0031_plan_row_defect_index
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0032_logistics"
down_revision: str | None = "0031_plan_row_defect_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRICE_KIND = sa.Enum("INDICATIVE", "PRICE", name="logistics_price_kind")


def upgrade() -> None:
    _PRICE_KIND.create(op.get_bind(), checkfirst=True)
    op.execute(
        sa.text(
            "INSERT INTO projects (id, key, name, created_at) "
            "VALUES (:id, :key, :name, now()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(id=uuid4(), key="logistics", name="Логистика")
    )


def downgrade() -> None:
    op.execute("DELETE FROM projects WHERE key = 'logistics'")
    _PRICE_KIND.drop(op.get_bind(), checkfirst=True)
