"""
Align the `l_rates` column comments with the renamed rate units.

The rate groups now carry per-tonne units («руб./т», «дол./т») and «Ставка за
1 тонну» is computed as the sum of the three editable rates, so the data
catalog labels changed. Comments are part of the DDL the migration guard
compares, so the database needs the same three strings — a new migration, not
an edit of the already-applied 0035.

Revision ID: 0039_logistics_rate_comments
Revises: 0038_renovation_blocked_role
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_logistics_rate_comments"
down_revision: str | None = "0038_renovation_blocked_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMENTS: dict[str, str] = {
    "ppr_rate_rub": "Ставка погрузочно-разгрузочных работ, руб./т",
    "delivery_rate_rub": "Отправка из пункта А до пункта Б, руб./т",
    "net_rate_rub": "Ставка за 1 тонну нетто без НДС, руб./т; сумма трёх ставок выше (вычисляется сервером)",
}


def upgrade() -> None:
    for column, comment in COMMENTS.items():
        op.alter_column(
            "l_rates",
            column,
            existing_type=None,
            comment=comment,
            schema=None,
        )


def downgrade() -> None:
    # Restore the 0035 wording verbatim.
    old = {
        "ppr_rate_rub": "Ставка погрузочно-разгрузочных работ, руб",
        "delivery_rate_rub": "Отправка из пункта А до пункта Б, руб",
        "net_rate_rub": "Ставка за 1 тонну нетто без НДС, руб",
    }
    for column, comment in old.items():
        op.alter_column("l_rates", column, existing_type=None, comment=comment, schema=None)
