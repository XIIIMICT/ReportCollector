"""
Move КППК copies to calendar dates and UUID organization references.

The old copies referenced the source integer keys (`org_id`/`period_id`).
This migration preserves every row id, resolves those keys to the normalized
application UUID/date values, makes realization volumes and excise zero-based,
and creates the slice-aware cost table.

Revision ID: 0012_kppk_calendar_cost_architecture
Revises: 0011_kppk_realization_buyout
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0012_kppk_calendar_cost"
down_revision: str | None = "0011_kppk_realization_buyout"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_MONEY = sa.Numeric(precision=20, scale=2)
_VOLUME = sa.Numeric(precision=20, scale=3)


def _migrate_realization_table(table: str, volume_columns: tuple[str, ...], excise_columns: tuple[str, ...]) -> None:
    op.drop_index(f"ix_{table}_scope", table_name=table)
    op.add_column(table, sa.Column("org_uuid", sa.Uuid(), nullable=True))
    op.add_column(table, sa.Column("period_date", sa.Date(), nullable=True))
    op.execute(
        sa.text(
            f"""
            UPDATE {table} r
            SET org_uuid = o.id,
                period_date = date_trunc('month', p.first_date)::date
            FROM kppk_orgs o, kppk_periods p
            WHERE o.num = r.org_id AND p.period_id = r.period_id
            """  # noqa: S608 — migration-owned identifiers
        )
    )
    op.alter_column(table, "org_uuid", nullable=False)
    op.alter_column(table, "period_date", nullable=False)
    for column in (*volume_columns, *excise_columns):
        op.alter_column(table, column, server_default=sa.text("0"))
        op.execute(sa.text(f"UPDATE {table} SET {column} = 0 WHERE {column} IS NULL"))  # noqa: S608 — migration-owned identifiers
        op.alter_column(table, column, nullable=False)
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_org_id_fkey"))
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_period_id_fkey"))
    op.drop_column(table, "org_id")
    op.drop_column(table, "period_id")
    op.alter_column(table, "org_uuid", new_column_name="org_id")
    op.create_foreign_key(f"{table}_org_id_fkey", table, "kppk_orgs", ["org_id"], ["id"], ondelete="RESTRICT")
    op.create_index(f"ix_{table}_scope", table, ["period_date", "org_id", "product"], unique=False)
    op.create_check_constraint(f"ck_{table}_calendar_month", table, "extract(day from period_date) = 1")


def upgrade() -> None:
    op.alter_column("kppk_orgs", "org_id", new_column_name="num")

    _migrate_realization_table(
        "kppk_realization",
        ("buyout_fact", "fact"),
        ("excise_fact",),
    )
    _migrate_realization_table(
        "kppk_realization_plan",
        ("buyout_bp_plan", "buyout_max_plan", "month_bp_plan", "month_max_plan"),
        ("excise_bp", "excise_max_plan"),
    )

    op.create_table(
        "kppk_cost",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("full_cost_bp", _MONEY, nullable=True),
        sa.Column("production_cost_bp", _MONEY, nullable=True),
        sa.Column("variable_cost_bp", _MONEY, nullable=True),
        sa.Column("full_cost_op", _MONEY, nullable=True),
        sa.Column("production_cost_op", _MONEY, nullable=True),
        sa.Column("variable_cost_op", _MONEY, nullable=True),
        sa.Column("full_cost_fact", _MONEY, nullable=True),
        sa.Column("production_cost_fact", _MONEY, nullable=True),
        sa.Column("variable_cost_fact", _MONEY, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["kppk_orgs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("org_id", "period_date", "product", name="uq_kppk_cost_scope"),
    )
    op.create_index("ix_kppk_cost_scope", "kppk_cost", ["period_date", "org_id", "product"], unique=False)
    op.create_check_constraint("ck_kppk_cost_calendar_month", "kppk_cost", "extract(day from period_date) = 1")
    op.create_table(
        "kppk_production_plan",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("rep_date", sa.Date(), nullable=False),
        sa.Column("load_id", sa.Integer(), nullable=True),
        sa.Column("product", sa.Text(), nullable=False),
        *(
            sa.Column(name, _VOLUME, nullable=True)
            for name in (
                "month_bp_plan",
                "month_plan",
                "goods",
                "stock",
                "stock_current",
                "stock_region",
                "stock_current_region",
                "stock_plant",
                "stock_current_plant",
            )
        ),
        *(sa.Column(name, _MONEY, nullable=True) for name in ("full_cost_bp", "full_cost_plan", "goods_bp_plan")),
        sa.Column("lastdate", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["kppk_orgs.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("extract(day from period_date) = 1", name="ck_kppk_production_plan_calendar_month"),
    )
    op.create_index("ix_kppk_production_plan_scope", "kppk_production_plan", ["period_date", "org_id", "product"])

    # Existing copies contain market rows. Reject conflicting nonzero costs
    # rather than silently choosing one market's production cost.
    for table, fields in (
        ("kppk_realization", ("full_cost_fact", "input_cost_fact", "var_exp_fact")),
        ("kppk_realization_plan", ("input_cost_bp", "input_cost_plan", "var_exp_bp_plan", "var_exp_plan")),
    ):
        for field in fields:
            op.execute(
                sa.text(f"""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM {table}
                        WHERE rep_date = (date_trunc('month', period_date) + interval '1 month - 1 day')::date
                        GROUP BY org_id, period_date, product
                        HAVING count(DISTINCT nullif({field}, 0)) > 1
                    ) THEN
                        RAISE EXCEPTION 'Разные ненулевые значения себестоимости: {table}.{field}';
                    END IF;
                END $$
            """)  # noqa: S608 — migration-owned identifiers
            )
    op.execute(
        sa.text("""
        INSERT INTO kppk_cost (
            id, org_id, period_date, product, full_cost_fact, production_cost_fact, variable_cost_fact
        )
        SELECT gen_random_uuid(), org_id, period_date, product,
               coalesce(max(nullif(full_cost_fact, 0)), 0),
               coalesce(max(nullif(input_cost_fact, 0)), 0),
               coalesce(max(nullif(var_exp_fact, 0)), 0)
        FROM kppk_realization
        WHERE rep_date = (period_date + interval '1 month - 1 day')::date
        GROUP BY org_id, period_date, product
    """)
    )
    op.execute(
        sa.text("""
        INSERT INTO kppk_cost (
            id, org_id, period_date, product, production_cost_bp, production_cost_op, variable_cost_bp, variable_cost_op
        )
        SELECT gen_random_uuid(), org_id, period_date, product,
               coalesce(max(nullif(input_cost_bp, 0)), 0), coalesce(max(nullif(input_cost_plan, 0)), 0),
               coalesce(max(nullif(var_exp_bp_plan, 0)), 0), coalesce(max(nullif(var_exp_plan, 0)), 0)
        FROM kppk_realization_plan
        WHERE rep_date = (period_date + interval '1 month - 1 day')::date
        GROUP BY org_id, period_date, product
        ON CONFLICT (org_id, period_date, product) DO UPDATE SET
            production_cost_bp = excluded.production_cost_bp, production_cost_op = excluded.production_cost_op,
            variable_cost_bp = excluded.variable_cost_bp, variable_cost_op = excluded.variable_cost_op
    """)
    )
    op.execute(sa.text("DROP TABLE kppk_periods"))


def downgrade() -> None:
    # Calendar months are reconstructible; source ids are recovered from the
    # source dictionary when available. New months receive unused integer ids.
    op.create_table(
        "kppk_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("month_name", sa.Text(), nullable=False),
        sa.Column("year_num", sa.Integer(), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("month_name", "year_num", name="uq_kppk_periods_month_year"),
    )
    op.execute(
        sa.text("""
        INSERT INTO kppk_periods
            (id, period_id, month_name, year_num, first_date, last_date, created_at, updated_at, updated_by)
        SELECT gen_random_uuid(), row_number() OVER (ORDER BY period_date),
            (ARRAY['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'])[extract(month from period_date)::integer],
            extract(year from period_date)::integer, period_date,
            (period_date + interval '1 month - 1 day')::date, now(), now(), 'downgrade'
        FROM (SELECT period_date FROM kppk_realization UNION SELECT period_date FROM kppk_realization_plan) months
    """)
    )
    # Dictionary entries created after migration may not have a source number.
    op.execute(
        sa.text("""
        WITH missing AS (
            SELECT id, row_number() OVER (ORDER BY id) + (SELECT coalesce(max(num), 0) FROM kppk_orgs) AS num
            FROM kppk_orgs WHERE num IS NULL
        ) UPDATE kppk_orgs o SET num = m.num FROM missing m WHERE o.id = m.id
    """)
    )
    for table, volumes, excises in (
        ("kppk_realization", ("buyout_fact", "fact"), ("excise_fact",)),
        (
            "kppk_realization_plan",
            ("buyout_bp_plan", "buyout_max_plan", "month_bp_plan", "month_max_plan"),
            ("excise_bp", "excise_max_plan"),
        ),
    ):
        op.drop_index(f"ix_{table}_scope", table_name=table)
        op.drop_constraint(f"ck_{table}_calendar_month", table, type_="check")
        op.add_column(table, sa.Column("source_org_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("period_id", sa.Integer(), nullable=True))
        op.execute(
            sa.text(f"""
            UPDATE {table} r SET source_org_id = o.num, period_id = p.period_id
            FROM kppk_orgs o, kppk_periods p WHERE o.id = r.org_id AND p.first_date = r.period_date
        """)
        )
        op.alter_column(table, "source_org_id", nullable=False)
        op.alter_column(table, "period_id", nullable=False)
        op.drop_constraint(f"{table}_org_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "org_id")
        op.drop_column(table, "period_date")
        op.alter_column(table, "source_org_id", new_column_name="org_id")
        op.create_foreign_key(f"{table}_org_id_fkey", table, "kppk_orgs", ["org_id"], ["num"], ondelete="RESTRICT")
        op.create_foreign_key(
            f"{table}_period_id_fkey", table, "kppk_periods", ["period_id"], ["period_id"], ondelete="RESTRICT"
        )
        op.create_index(f"ix_{table}_scope", table, ["period_id", "org_id", "product"])
        for column in (*volumes, *excises):
            op.alter_column(table, column, nullable=True, server_default=None)
    op.drop_index("ix_kppk_cost_scope", table_name="kppk_cost")
    op.drop_table("kppk_cost")
    op.drop_index("ix_kppk_production_plan_scope", table_name="kppk_production_plan")
    op.drop_table("kppk_production_plan")
    op.alter_column("kppk_orgs", "num", new_column_name="org_id")
