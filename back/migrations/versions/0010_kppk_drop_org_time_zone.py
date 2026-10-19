"""
Drop the time zone of the «ДЗО» copies.

The program copy `kppk_orgs` keeps the source id, code and name only: the time
zone is neither stored nor shown, so the seeder stops copying
`raw.kppk_d_org.time_zone` and the column goes away.

Revision ID: 0010_kppk_drop_org_time_zone
Revises: 0009_kppk_unique_constraints
Create Date: 2026-10-17 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_kppk_drop_org_time_zone"
down_revision: str | None = "0009_kppk_unique_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("kppk_orgs", "time_zone")


def downgrade() -> None:
    op.add_column(
        "kppk_orgs",
        sa.Column("time_zone", sa.Integer(), server_default="0", nullable=False),
    )
