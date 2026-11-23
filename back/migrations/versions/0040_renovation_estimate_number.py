"""
Reintroduce plan column col_218 as «Номер сметы» (the КСиР block).

The original col_218 («Корректировка») was retired and dropped by
0007_drop_plan_correction, then kept in the form JSON only as a HIDDEN field
so that no later form position had to shift. The customer replaces it with a
real, editable text field — «Номер сметы» — inserted as the FIRST column of
the «2) Дирекция КСиР» block (displayed column 31). The db name is reused:
there was never any business data in the retired column, and adding a fresh
one keeps the col_002..col_218 naming scheme intact.

Comments are part of the DDL the migration guard compares, so the new column
carries exactly the string field_header_paths() derives from the form header
chain. A new migration, not an edit of the already-applied 0007.

Revision ID: 0040_renovation_estimate_number
Revises: 0039_logistics_rate_comments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_renovation_estimate_number"
down_revision: str | None = "0039_logistics_rate_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMENT = "2) Дирекция КСиР → Номер сметы"


def upgrade() -> None:
    # 0007 dropped the column; there is nothing to preserve or convert.
    op.add_column(
        "r_plan_rows",
        sa.Column("col_218", sa.Text(), nullable=True, comment=COMMENT),
    )


def downgrade() -> None:
    # The field is optional and held no important data; dropping loses nothing.
    op.drop_column("r_plan_rows", "col_218")
