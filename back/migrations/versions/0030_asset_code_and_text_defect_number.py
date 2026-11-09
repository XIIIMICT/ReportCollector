"""
Make the defect-statement number text and collapse the asset code pair.

`r_assets` carried two codes: an internal slug (`code`, e.g. «vos») and the
official repair code of the renovation ID (`repair_code`, e.g. «VOZR»). The slug
was never user input — it only keyed the seed — and every lookup already goes
through the repair code, so the slug is dropped and the repair code takes the
name `code`.

`r_plan_rows.col_023` («Дефектная ведомость/ВОР №») was NUMERIC: a seven-digit
number with a meaningful leading zero, which a number cannot keep. The column
becomes TEXT and the stored values are re-rendered as digits (`706179` →
`0706179`), the same normalisation the row editor and the importer apply.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_asset_code_and_text_defect_number"
down_revision = "0029_data_job_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `repair_code` takes the name `code`. The old column carried a UNIQUE
    # CONSTRAINT plus a plain unique index; the ORM declares one unique index, so
    # both old objects are dropped and a unique index is created on the new name.
    op.drop_constraint("r_assets_repair_code_key", "r_assets", type_="unique")
    op.drop_index("ix_r_assets_code", table_name="r_assets")
    op.drop_column("r_assets", "code")
    op.alter_column("r_assets", "repair_code", new_column_name="code", existing_type=sa.Text())
    op.alter_column("r_assets", "code", comment="Код предприятия в идентификационном коде работы/ремонта")
    op.create_index("ix_r_assets_code", "r_assets", ["code"], unique=True)

    # `::numeric::text` would render `706179.0000`; the integer cast drops the
    # scale Excel/psycopg put there, and lpad restores the dropped leading zero.
    op.execute(
        """
        ALTER TABLE r_plan_rows
        ALTER COLUMN col_023 TYPE text
        USING CASE
            WHEN col_023 IS NULL THEN NULL
            ELSE lpad(col_023::numeric::bigint::text, 7, '0')
        END
        """
    )


def downgrade() -> None:
    # A value that is not a number cannot go back into NUMERIC; such rows become
    # NULL rather than aborting the downgrade.
    op.execute(
        """
        ALTER TABLE r_plan_rows
        ALTER COLUMN col_023 TYPE numeric(18, 4)
        USING CASE
            WHEN col_023 ~ '^\\s*-?[0-9]+\\s*$' THEN col_023::numeric
            ELSE NULL
        END
        """
    )

    op.drop_index("ix_r_assets_code", table_name="r_assets")
    op.alter_column("r_assets", "code", new_column_name="repair_code", existing_type=sa.Text())
    op.alter_column("r_assets", "repair_code", comment="Код предприятия в идентификационном коде работы/ремонта.")
    op.create_unique_constraint("r_assets_repair_code_key", "r_assets", ["repair_code"])
    # The slug is restored from the legacy table: its first 32 characters are
    # that asset's legacy code, so a seeded database comes back unchanged.
    op.add_column("r_assets", sa.Column("code", sa.Text(), nullable=True))
    op.execute("UPDATE r_assets SET code = left(repair_code, 32)")
    op.alter_column("r_assets", "code", nullable=False)
    op.create_index("ix_r_assets_code", "r_assets", ["code"], unique=True)
    op.alter_column("r_assets", "code", comment="Системный код предприятия для импорта исходных планов.")
