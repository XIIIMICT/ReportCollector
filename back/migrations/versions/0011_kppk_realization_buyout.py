"""
Add the «Выкуп / Объем» columns ("новая фигня") of the «Реализация» form.

The Таблицы.xlsx template renders the buyout volume per slice, so each slice
of the plan table gets its own column (по образцу остальных срезов:
fca_price_bp_plan / fca_price_max_plan) and the fact table its own:

- kppk_realization_plan.buyout_bp_plan   (срез «Бизнес-план»)
- kppk_realization_plan.buyout_max_plan  (срез «Оперплан»)
- kppk_realization.buyout_fact           (срез «Факт»)

The source dumps don't hold the columns yet — the copies start them NULL and
the seeder keeps copying verbatim.

Revision ID: 0011_kppk_realization_buyout
Revises: 0010_kppk_drop_org_time_zone
Create Date: 2026-10-17 18:30:00.000000

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0011_kppk_realization_buyout"
down_revision: str | None = "0010_kppk_drop_org_time_zone"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BUYOUT_TYPE = sa.Numeric(precision=20, scale=3)


def upgrade() -> None:
    op.add_column("kppk_realization_plan", sa.Column("buyout_bp_plan", _BUYOUT_TYPE, nullable=True))
    op.add_column("kppk_realization_plan", sa.Column("buyout_max_plan", _BUYOUT_TYPE, nullable=True))
    op.add_column("kppk_realization", sa.Column("buyout_fact", _BUYOUT_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("kppk_realization", "buyout_fact")
    op.drop_column("kppk_realization_plan", "buyout_max_plan")
    op.drop_column("kppk_realization_plan", "buyout_bp_plan")
