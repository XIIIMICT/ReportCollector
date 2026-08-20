"""
Unique name / repair code on the renovation assets.

The renovation assets are legal entities: a name and a repair code must be
unique on their own (not just as a pair). Existing schemas that already
contain the duplicated repair code BSKR (the seeded «АО «БСЗ»» row) get it
renamed to BSZR first, then the two unique constraints are added. Fresh
schemas already have the constraints via create_all (SQLModel metadata); the
adds are guarded the same way as 0008.

Revision ID: 0009_renovation_asset_unique
Revises: 0008_renovation_dictionary_audit
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_renovation_asset_unique"
down_revision: str | None = "0008_renovation_dictionary_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _constraint_exists(bind: sa.engine.Connection, table: str, name: str) -> bool:
    if table not in set(sa.inspect(bind).get_table_names()):
        return False
    return any(constraint["name"] == name for constraint in sa.inspect(bind).get_unique_constraints(table))


def _add_unique_if_missing(table: str, name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, table, name):
        op.create_unique_constraint(name, table, columns)


def upgrade() -> None:
    op.execute("UPDATE r_assets SET repair_code = 'BSZR' WHERE code = 'bsz' AND repair_code = 'BSKR'")
    _add_unique_if_missing("r_assets", "r_assets_name_key", ["name"])
    _add_unique_if_missing("r_assets", "r_assets_repair_code_key", ["repair_code"])


def downgrade() -> None:
    bind = op.get_bind()
    if _constraint_exists(bind, "r_assets", "r_assets_name_key"):
        op.drop_constraint("r_assets_name_key", "r_assets", type_="unique")
    if _constraint_exists(bind, "r_assets", "r_assets_repair_code_key"):
        op.drop_constraint("r_assets_repair_code_key", "r_assets", type_="unique")
