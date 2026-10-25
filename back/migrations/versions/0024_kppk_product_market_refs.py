"""
Normalize the КППК realization copies: product and market become uuid references.

Why: the source dumps carried a product NAME and a market CODE as free text in
every realization row (`raw.kppk_f_realization*`: `product text`, `region text`),
and migration 0012 normalized only the organization. Half-normalized is the worst
state: the dictionary was already referenced by uuid, but a product still lived
as a string in 300k rows, so

- renaming a product in «Продуктовые позиции» orphaned its history silently
  (no foreign key to notice);
- a typo in a source row was a different product with no way to tell;
- deleting a product physically broke the link for every realization row;
- the seeder had to match products by name in two places (the matrix and the
  period rows), each an opportunity to drift.

What:
1. a `kppk_markets` dictionary (code + name) replaces the `MARKET_CODES`
   constant and the `region` text column;
2. `product_id` / `market_id` are added to `kppk_realization`,
   `kppk_realization_plan`, `kppk_cost` and `kppk_production_plan`;
3. they are back-filled by matching the stored text against the dictionaries —
   the ONLY place a name comparison happens, and it happens once;
4. the migration ABORTS when any row cannot be matched: a silent skip would
   leave rows without a product, which is exactly the state this replaces;
5. the text columns are dropped and the FK/NOT NULL constraints take over.

The pre-normalization state has no meaning to preserve: at this stage nobody has
entered data through the forms yet, the copies mirror the source, and the seeder
rebuilds them from scratch anyway (`just seed-kppk`). The unique key of
«Себестоимость» moves from `(org_id, period_date, product)` to
`(org_id, period_date, product_id)` — the same guarantee, on the normalized key.

Revision ID: 0024_kppk_product_market_refs
Revises: 0023_kppk_nullable_volume_excise
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_kppk_product_market_refs"
down_revision: str | None = "0023_kppk_nullable_volume_excise"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The markets of the form, in the order the source listed them. The code is what
# the grid prints; the name is the full wording used in the dictionary editor.
_MARKETS: tuple[tuple[str, str], ...] = (
    ("РФ", "Российская Федерация"),
    ("ДЗ", "Дальнее зарубежье"),
    ("БЗ", "Ближнее зарубежье"),
)

# Tables that reference the dictionaries, and whether they also carry a market.
_TABLES: tuple[tuple[str, bool], ...] = (
    ("kppk_realization", True),
    ("kppk_realization_plan", True),
    ("kppk_cost", False),
    ("kppk_production_plan", False),
)


def _set_comment(connection: sa.Connection, table: str, column: str, comment: str) -> None:
    """Attach a catalog comment to a column; `COMMENT ON` takes no bound parameters."""
    literal = "'" + comment.replace("'", "''") + "'"
    connection.execute(sa.text(f"COMMENT ON COLUMN {table}.{column} IS {literal}"))


def _set_table_comment(connection: sa.Connection, table: str, comment: str) -> None:
    """Attach a catalog comment to a table."""
    literal = "'" + comment.replace("'", "''") + "'"
    connection.execute(sa.text(f"COMMENT ON TABLE {table} IS {literal}"))


def upgrade() -> None:
    connection = op.get_bind()

    # 1. The market dictionary.
    op.create_table(
        "kppk_markets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updater_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["updater_user_id"], ["app_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    for code, name in _MARKETS:
        connection.execute(
            sa.text(
                "INSERT INTO kppk_markets (id, code, name, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :code, :name, now(), now())"
            ),
            {"code": code, "name": name},
        )
    # The catalog comments travel with the model, but this migration creates the
    # table in raw DDL — without them the drift check (`compare_metadata` against
    # `SQLModel.metadata`) reports every column as a modification.
    _set_table_comment(
        connection,
        "kppk_markets",
        "КППК → Справочник рынков сбыта формы «Реализация» (РФ, ДЗ, БЗ)",
    )
    _set_comment(connection, "kppk_markets", "code", "Короткий код рынка сбыта, как в исходной таблице: РФ, ДЗ, БЗ")
    _set_comment(connection, "kppk_markets", "name", "Полное наименование рынка сбыта")
    _set_comment(connection, "kppk_markets", "updater_user_id", "ID автора последнего изменения записи → app_users.id")

    # 2. The reference columns, nullable first so the back-fill can fill them.
    for table, has_market in _TABLES:
        op.add_column(table, sa.Column("product_id", sa.Uuid(), nullable=True))
        if has_market:
            op.add_column(table, sa.Column("market_id", sa.Uuid(), nullable=True))

    # 3. Back-fill. The text columns still exist at this point — this is the one
    #    and only place where a name is matched against the dictionary.
    for table, has_market in _TABLES:
        connection.execute(
            sa.text(
                f"UPDATE {table} r SET product_id = p.id "  # noqa: S608 — table names are literals of this module
                "FROM kppk_products p WHERE p.name = r.product"
            )
        )
        if has_market:
            connection.execute(
                sa.text(
                    f"UPDATE {table} r SET market_id = m.id "  # noqa: S608 — table names are literals of this module
                    "FROM kppk_markets m WHERE m.code = r.region"
                )
            )
        # 4. Refuse to guess: an unmatched row would end up without a product.
        orphans = connection.execute(
            sa.text(
                f"SELECT count(*) FROM {table} "  # noqa: S608 — table names are literals of this module
                "WHERE product_id IS NULL" + (" OR market_id IS NULL" if has_market else "")
            )
        ).scalar_one()
        if orphans:
            message = (
                f"Нормализация {table}: {orphans} строк не находят продукт"
                + (" или рынок" if has_market else "")
                + " в справочниках — сначала приведите имена в соответствие."
            )
            raise RuntimeError(message)

    # 5. Not-null, foreign keys, then drop the text columns they replace.
    #    The catalog comments are applied at the end (step 6): `add_column` here
    #    is raw DDL, so without them the drift check reports every new column.
    for table, has_market in _TABLES:
        op.alter_column(table, "product_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_product",
            table,
            "kppk_products",
            ["product_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        if has_market:
            op.alter_column(table, "market_id", nullable=False)
            op.create_foreign_key(
                f"fk_{table}_market",
                table,
                "kppk_markets",
                ["market_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    # Indexes that named the text columns are replaced by the model's own.
    op.drop_index("ix_kppk_realization_scope", table_name="kppk_realization")
    op.drop_index("ix_kppk_realization_region_product", table_name="kppk_realization")
    op.drop_index("ix_kppk_realization_plan_scope", table_name="kppk_realization_plan")
    op.drop_index("ix_kppk_realization_plan_region_product", table_name="kppk_realization_plan")
    op.drop_index("ix_kppk_cost_scope", table_name="kppk_cost")
    op.drop_index("ix_kppk_production_plan_scope", table_name="kppk_production_plan")
    # «Себестоимость» keeps its natural key, now on the normalized product.
    op.drop_constraint("uq_kppk_cost_scope", "kppk_cost", type_="unique")
    op.create_unique_constraint("uq_kppk_cost_scope", "kppk_cost", ["org_id", "period_date", "product_id"])

    op.drop_column("kppk_realization", "product")
    op.drop_column("kppk_realization", "region")
    op.drop_column("kppk_realization_plan", "product")
    op.drop_column("kppk_realization_plan", "region")
    op.drop_column("kppk_cost", "product")
    op.drop_column("kppk_production_plan", "product")

    op.create_index("ix_kppk_realization_scope", "kppk_realization", ["period_date", "org_id", "product_id"])
    op.create_index("ix_kppk_realization_region_product", "kppk_realization", ["market_id", "product_id"])
    op.create_index("ix_kppk_realization_plan_scope", "kppk_realization_plan", ["period_date", "org_id", "product_id"])
    op.create_index(
        "ix_kppk_realization_plan_region_product",
        "kppk_realization_plan",
        ["market_id", "product_id"],
    )
    op.create_index("ix_kppk_cost_scope", "kppk_cost", ["period_date", "org_id", "product_id"])
    op.create_index("ix_kppk_production_plan_scope", "kppk_production_plan", ["period_date", "org_id", "product_id"])

    # 6. Catalog comments on the new reference columns. The model carries them,
    #    but the columns are added in raw DDL: the drift check compares comments
    #    too, and `COMMENT ON` takes no bound parameters.
    for table, has_market in _TABLES:
        _set_comment(
            connection,
            table,
            "product_id",
            "ID продукта → kppk_products.id; заменил текстовое имя из источника",
        )
        if has_market:
            _set_comment(
                connection,
                table,
                "market_id",
                "ID рынка сбыта → kppk_markets.id; заменил текстовый код региона из источника",
            )


def downgrade() -> None:
    """
    Restore the text columns from the dictionaries, and their indexes with them.

    The values can be rebuilt exactly: every row still points at the product and
    market it referenced, so the names and codes are read back from them. The
    indexes and the «Себестоимость» unique key move back onto the text columns —
    `upgrade` dropped them, so leaving them out would downgrade to a schema the
    earlier migrations never produced (and a later re-upgrade would fail on a
    missing index).
    """
    connection = op.get_bind()

    # The index / unique-key names are shared by both shapes, so the normalized
    # ones go first, right before their text replacements are created below.
    op.drop_index("ix_kppk_realization_scope", table_name="kppk_realization")
    op.drop_index("ix_kppk_realization_region_product", table_name="kppk_realization")
    op.drop_index("ix_kppk_realization_plan_scope", table_name="kppk_realization_plan")
    op.drop_index("ix_kppk_realization_plan_region_product", table_name="kppk_realization_plan")
    op.drop_index("ix_kppk_cost_scope", table_name="kppk_cost")
    op.drop_index("ix_kppk_production_plan_scope", table_name="kppk_production_plan")
    op.drop_constraint("uq_kppk_cost_scope", "kppk_cost", type_="unique")

    for table, has_market in _TABLES:
        op.add_column(table, sa.Column("product", sa.Text(), nullable=True))
        if has_market:
            op.add_column(table, sa.Column("region", sa.Text(), nullable=True))

    for table, has_market in _TABLES:
        connection.execute(
            sa.text(
                f"UPDATE {table} r SET product = p.name "  # noqa: S608 — table names are literals of this module
                "FROM kppk_products p WHERE p.id = r.product_id"
            )
        )
        if has_market:
            connection.execute(
                sa.text(
                    f"UPDATE {table} r SET region = m.code "  # noqa: S608 — table names are literals of this module
                    "FROM kppk_markets m WHERE m.id = r.market_id"
                )
            )

    for table, has_market in _TABLES:
        op.alter_column(table, "product", nullable=False)
        if has_market:
            op.alter_column(table, "region", nullable=False)
        op.drop_constraint(f"fk_{table}_product", table, type_="foreignkey")
        op.drop_column(table, "product_id")
        if has_market:
            op.drop_constraint(f"fk_{table}_market", table, type_="foreignkey")
            op.drop_column(table, "market_id")

    # Recreate the pre-normalization indexes and the text-column unique key.
    op.create_index("ix_kppk_realization_scope", "kppk_realization", ["period_date", "org_id", "product"])
    op.create_index("ix_kppk_realization_region_product", "kppk_realization", ["region", "product"])
    op.create_index("ix_kppk_realization_plan_scope", "kppk_realization_plan", ["period_date", "org_id", "product"])
    op.create_index("ix_kppk_realization_plan_region_product", "kppk_realization_plan", ["region", "product"])
    op.create_index("ix_kppk_cost_scope", "kppk_cost", ["period_date", "org_id", "product"])
    op.create_index("ix_kppk_production_plan_scope", "kppk_production_plan", ["period_date", "org_id", "product"])
    op.create_unique_constraint("uq_kppk_cost_scope", "kppk_cost", ["org_id", "period_date", "product"])

    op.drop_table("kppk_markets")
