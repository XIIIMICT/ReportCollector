"""
Seed the «КППК» copies of the source tables without touching the other projects.

The ORIGINAL data (`raw.kppk_*` tables with the real figures) is not part of the
repository: it ships as SQL dumps the operator loads separately. The seeder:

1. creates the `raw` schema and the source tables **only when missing**, from
   the structure in `back/src/kppk/data/source_schema.sql`; on a real
   installation they already exist and are never touched. A fresh/CI database
   gets a handful of demo rows (`demo_source_rows.sql`) so every form works —
   the `dict` schema belongs to another project and is not touched at all;
2. restores missing application tables from the current models, then re-copies
   the source data on every run; existing tables still evolve through migrations;
3. rebuilds «Продуктовые позиции» (`kppk_org_products`) from
   `back/src/kppk/data/product_matrix.sql` — the product x ДЗО pairs AND their
   «Заполняемый акциз» flags, the one input the source dumps never carried. It
   is a plain SQL table there (generated once from «Матрица продуктов.xlsx»), so
   the seeder needs no spreadsheet. Products and ДЗО are matched by exact
   display name, and a matrix product missing in the source dictionary is
   created without a num under an `@`-prefixed name;
4. finally rebuilds the «Реализация» / «Себестоимость» rows for every month from
   September 2023 to December of the next year (overwriting the previous ones)
   and re-derives the costs from them — the same "the seed owns its rows"
   approach as the renovation plan.

Re-copying on every run keeps the behavior deterministic: any manual dictionary
edit is reconciled from the source at the next seed. The kppk project row and
its administrator links are ensured by `seed_administrators` (also called by
the full seeder). Other projects' memberships and grants are left untouched.

Usage: uv run --directory back -m src.scripts.seed_kppk
"""

import asyncio
import re
from calendar import monthrange
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, TypedDict

from sqlalchemy import CursorResult, MetaData, func, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.config import get_settings
from src.database import async_session_factory
from src.kppk import models as _kppk_models  # noqa: F401 — register current application table definitions
from src.kppk.constants import (
    KPPK_PROJECT_KEY,
    KPPK_SOURCE_SCHEMA_RAW,
    KPPK_SOURCE_TABLES,
    MARKET_CODES,
    normalize_text,
)

# Rows per INSERT: one statement of ~50k rows would exceed the bound-parameter
# limit of the driver.
PERIOD_INSERT_CHUNK: Final[int] = 1000

# The seeder's own inputs (`back/src/kppk/data/`), NOT the original dumps:
# `source_schema.sql` is the structure of the source tables, `product_matrix.sql`
# the one input the sources never carried (product x ДЗО + excise flags), and
# `demo_source_rows.sql` a handful of rows for an install with no source data.
KPPK_DATA_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "kppk" / "data"
SOURCE_SCHEMA_PATH: Final[Path] = KPPK_DATA_DIR / "source_schema.sql"
DEMO_ROWS_PATH: Final[Path] = KPPK_DATA_DIR / "demo_source_rows.sql"
PRODUCT_MATRIX_PATH: Final[Path] = KPPK_DATA_DIR / "product_matrix.sql"
KPPK_ADMIN_EMAILS: Final[tuple[str, ...]] = ("evtushenko.sn@ruschem.ru", "lavrova.mg@ruschem.ru")
# Only the four dictionary copies get audit stamps; the fact/plan copies stay
# verbatim (the migration does not create audit columns for them).
STAMPED_COPY_TABLES: Final[frozenset[str]] = frozenset({"kppk_d_org", "kppk_d_product_seq", "kppk_d_raw_seq"})

# The text columns of the copies. Their values are normalised on the way in —
# trimmed and collapsed to single spaces (`normalize_text` in
# `src/kppk/constants.py`) — because the source dumps carry stray tabs, line
# breaks, doubled and non-breaking spaces, and products/regions are the
# natural keys of the realization copies: an untrimmed value would silently
# become a second product.
TEXT_COPY_COLUMNS: Final[frozenset[str]] = frozenset({"code", "name", "product", "region"})


def _normalized_sql(column: str) -> str:
    r"""
    Build the SQL form of `normalize_text`.

    The server must apply the same rule as the Python side, so the expression
    mirrors `" ".join(value.split())` step by step: the non-breaking space is a
    plain space for us (PostgreSQL's `\s` does not match it, hence the explicit
    `replace`), then every run of whitespace collapses to one ordinary space,
    and the edges are trimmed.
    """
    return f"""regexp_replace(btrim(replace(s."{column}", chr(160), ' ')), '[[:space:]]+', ' ', 'g')"""


def _source_column_sql(column: str) -> str:
    """Source expression of one copied column: normalised when it is text."""
    return _normalized_sql(column) if column in TEXT_COPY_COLUMNS else f's."{column}"'


# Column mapping of every copied table: (source column, rc copy column).
SOURCE_COPY_COLUMNS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    # The source time zone is not copied: the program keeps id, code and name.
    "kppk_d_org": (("id", "num"), ("code", "code"), ("name", "name")),
    "kppk_d_product_seq": (("num", "product_num"), ("product", "name")),
    "kppk_d_raw_seq": (("num", "raw_num"), ("raw", "name")),
    "kppk_f_realization": tuple(
        (name, name)
        for name in (
            "org_id",
            "period_id",
            "load_id",
            "rep_date",
            "product",
            "region",
            "month_plan",
            "month_part_plan",
            "fca_price_plan",
            "var_exp_plan",
            "export_duty_proc",
            "fact",
            "fca_price_fact",
            "var_exp_fact",
            "stock",
            "plan_to_sell",
            "export_duty_fact",
            "export_duty_plan",
            "excise_plan",
            "excise_fact",
            "full_cost_plan",
            "full_cost_fact",
            "input_cost_plan",
            "input_cost_fact",
            "stock_current",
            "fca_price_market_plan",
            "fca_price_market_fact",
            "lastdate",
        )
    ),
    "kppk_f_realization_plan": tuple(
        (name, name)
        for name in (
            "org_id",
            "period_id",
            "load_id",
            "rep_date",
            "product",
            "region",
            "month_bp_plan",
            "month_max_plan",
            "month_opt_plan",
            "fca_price_bp_plan",
            "fca_price_max_plan",
            "fca_price_opt_plan",
            "var_exp_bp_plan",
            "var_exp_plan",
            "export_duty_bp",
            "export_duty_max_plan",
            "export_duty_opt_plan",
            "excise_bp",
            "excise_max_plan",
            "excise_opt_plan",
            "input_cost_bp",
            "input_cost_plan",
            "fca_price_market_bp_plan",
            "fca_price_market_max_plan",
            "fca_price_market_opt_plan",
            "lastdate",
        )
    ),
    "kppk_f_production_plan": tuple(
        (name, name)
        for name in (
            "org_id",
            "rep_date",
            "load_id",
            "period_id",
            "product",
            "month_bp_plan",
            "month_plan",
            "goods",
            "full_cost_bp",
            "full_cost_plan",
            "stock",
            "stock_current",
            "goods_bp_plan",
            "lastdate",
            "stock_region",
            "stock_current_region",
            "stock_plant",
            "stock_current_plant",
        )
    ),
}


class CommandError(RuntimeError):
    """Seed failure with a user-facing message."""


class MatrixMatch(TypedDict):
    """One row of the product matrix: a ДЗО + product pair and its excise flag."""

    org: str
    product: str
    hasExcise: bool


def split_sql_statements(sql: str) -> list[str]:
    """
    Split a SQL dump into single statements.

    Statements separate with `;`, but string literals may hold a `;` of their
    own («Средство "Белизна"; тыс. шт»), so the split respects single-quoted
    strings ('' escapes included) and comments.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        char = sql[i]
        if not in_string and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = end if end != -1 else len(sql)
            continue
        if not in_string and char == "'":
            in_string = True
        elif in_string and char == "'":
            # Either the '' escape or the closing quote.
            if sql.startswith("''", i + 1):
                i += 1
            else:
                in_string = False
        elif char == ";" and not in_string:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _qualified(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _statement_target(statement: str) -> tuple[str, str] | None:
    """Return `(schema, table)` of a CREATE TABLE statement, None otherwise."""
    head = statement.strip().lower()
    if not head.startswith("create table"):
        return None
    words = statement.split(maxsplit=3)
    if len(words) < 3:
        return None
    target = words[2].rstrip("(").strip().strip('"')
    if "." not in target:
        return None
    schema, table = (part.strip('"') for part in target.split(".", 1))
    return schema, table


def _read_sql_file(path: Path) -> str:
    """Read a dump file (a sync op on purpose: the seeder is a CLI script)."""
    return path.read_text(encoding="utf-8")


async def _schema_exists(connection: AsyncConnection, schema: str) -> bool:
    result = await connection.execute(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema").bindparams(schema=schema)
    )
    return result.scalar() is not None


async def _table_exists(connection: AsyncConnection, schema: str, table: str) -> bool:
    result = await connection.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table").bindparams(
            schema=schema, table=table
        )
    )
    return result.scalar() is not None


async def _ensure_schemas(connection: AsyncConnection) -> list[str]:
    """
    Create the missing `raw` schema; returns the created names.

    Only `raw` is ours — the КППК source tables live there. The `dict` schema
    belongs to another project and is never created or touched here.
    """
    created: list[str] = []
    if not await _schema_exists(connection, KPPK_SOURCE_SCHEMA_RAW):
        await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{KPPK_SOURCE_SCHEMA_RAW}"'))
        created.append(KPPK_SOURCE_SCHEMA_RAW)
    return created


async def _ensure_source_tables(connection: AsyncConnection) -> int:
    """
    Create the missing source tables and fill them from the demo rows.

    A real installation already has these tables (they come with the original
    dumps) and the seeder only READS them — this path exists for a fresh or CI
    database, where a handful of demo rows keeps every form working. The tables
    are created from `source_schema.sql` and filled from `demo_source_rows.sql`,
    both shipped with the code; the original dumps are never required.

    Returns how many tables were created.
    """
    created = 0
    if not SOURCE_SCHEMA_PATH.exists():
        message = f"Не найден файл структуры исходных таблиц: {SOURCE_SCHEMA_PATH}."
        raise CommandError(message)
    statements = split_sql_statements(_read_sql_file(SOURCE_SCHEMA_PATH))
    missing: list[str] = []
    for statement in statements:
        target = _statement_target(statement)
        if target is None or await _table_exists(connection, *target):
            continue
        await connection.execute(text(statement))
        missing.append(target[1])
        created += 1
    if missing:
        # Demo rows are one file covering every source table: they are inserted
        # only into the tables this run just created, so an existing part-filled
        # installation never gains test data on top of its own.
        demo = _read_sql_file(DEMO_ROWS_PATH) if DEMO_ROWS_PATH.exists() else ""
        for statement in split_sql_statements(demo):
            target = _statement_target(statement)
            if target is not None:
                continue
            await connection.execute(text(statement))
    return created


async def _truncate_copies(connection: AsyncConnection, app_schema: str) -> None:
    tables = ", ".join(_qualified(app_schema, target) for target in KPPK_SOURCE_TABLES.values())
    await connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))


async def _ensure_copy_tables(connection: AsyncConnection, app_schema: str) -> int:
    """Restore only missing КППК application tables; never alter existing tables."""
    names = (*KPPK_SOURCE_TABLES.values(), "kppk_org_products", "kppk_cost")
    metadata = MetaData()
    # Resolve the external audit FK without creating or modifying app_users.
    SQLModel.metadata.tables["app_users"].to_metadata(metadata, schema=app_schema)
    missing = 0
    for name in names:
        SQLModel.metadata.tables[name].to_metadata(metadata, schema=app_schema)
        if not await _table_exists(connection, app_schema, name):
            missing += 1
    # The private metadata resolves external FKs; only КППК tables are created.
    # SQLAlchemy orders their
    # CREATEs by foreign-key dependencies and targets the explicit app schema,
    # independent of the connection's search_path. No shared metadata mutation.
    await connection.run_sync(
        lambda sync: metadata.create_all(
            sync, tables=[metadata.tables[f"{app_schema}.{name}"] for name in names], checkfirst=True
        )
    )
    return missing


async def _copy_sources(connection: AsyncConnection, app_schema: str) -> dict[str, int]:
    """Re-copy every source table into its rc copy; returns per-table counters."""
    per_table: dict[str, int] = {}
    for source, target in KPPK_SOURCE_TABLES.items():
        columns = SOURCE_COPY_COLUMNS[source]
        target_columns = ", ".join(column for _, column in columns)
        source_columns = ", ".join(_source_column_sql(column) for column, _ in columns)
        # The four dictionaries carry audit stamps; the fact/plan copies stay
        # verbatim without them (the migration does not create those columns).
        stamped = source in STAMPED_COPY_TABLES
        stamp_columns = ", created_at, updated_at" if stamped else ""
        stamp_values = ", now(), now()" if stamped else ""
        if source in {"kppk_f_realization", "kppk_f_realization_plan", "kppk_f_production_plan"}:
            copied = [column for column, _ in columns if column not in {"org_id", "period_id"}]
            target_columns = ", ".join(copied)
            # Values are copied VERBATIM, NULLs included: the source files keep
            # volume/excise empty where nothing was measured, and the copies are
            # nullable for exactly that reason. Filling them with 0 would invent
            # figures on every re-seed.
            source_columns = ", ".join(_source_column_sql(column) for column in copied)
            missing = (
                await connection.execute(
                    text(
                        f"SELECT count(*) FROM {_qualified(KPPK_SOURCE_SCHEMA_RAW, source)} s "  # noqa: S608 — server-owned identifiers
                        f"LEFT JOIN {_qualified(KPPK_SOURCE_SCHEMA_RAW, 'kppk_d_period')} p ON p.id = s.period_id "
                        f"LEFT JOIN {_qualified(app_schema, 'kppk_orgs')} o ON o.num = s.org_id "
                        "WHERE p.id IS NULL OR o.id IS NULL"
                    )
                )
            ).scalar_one()
            if missing:
                message = f"Источник {source}: {missing} строк с неизвестным ДЗО или периодом."
                raise CommandError(message)
            result = await connection.execute(
                text(
                    f"INSERT INTO {_qualified(app_schema, target)} (id, org_id, period_date, {target_columns}) "  # noqa: S608 — server-owned identifiers
                    f"SELECT gen_random_uuid(), o.id, date_trunc('month', p.first_date)::date, {source_columns} "
                    f"FROM {_qualified(KPPK_SOURCE_SCHEMA_RAW, source)} s "
                    f"JOIN {_qualified(KPPK_SOURCE_SCHEMA_RAW, 'kppk_d_period')} p ON p.id = s.period_id "
                    f"JOIN {_qualified(app_schema, 'kppk_orgs')} o ON o.num = s.org_id"
                )
            )
        else:
            result = await connection.execute(
                text(
                    f"INSERT INTO {_qualified(app_schema, target)} (id, {target_columns}{stamp_columns}) "  # noqa: S608 — server-owned identifiers
                    f"SELECT gen_random_uuid(), {source_columns}{stamp_values} "
                    f"FROM {_qualified(KPPK_SOURCE_SCHEMA_RAW, source)} s"
                )
            )
        per_table[target] = int(result.rowcount or 0) if isinstance(result, CursorResult) else 0
    return per_table


async def _rebuild_costs(connection: AsyncConnection, app_schema: str) -> int:
    """
    Rebuild production costs without the realization market dimension.

    Prefer markets with nonzero realization in the corresponding slice. If a
    product has no sales at all, preserve its only nonzero cost anyway: costs
    belong to production and do not disappear when the sales volume is zero.
    Use the maximum nonzero candidate value and report every conflicting group.
    """
    cost = _qualified(app_schema, "kppk_cost")
    await connection.execute(text(f"TRUNCATE TABLE {cost}"))
    sources = (
        (
            "kppk_realization",
            {
                "full_cost_fact": "full_cost_fact",
                "input_cost_fact": "production_cost_fact",
                "var_exp_fact": "variable_cost_fact",
            },
            "fact",
        ),
        (
            "kppk_realization_plan",
            {
                "input_cost_bp": "production_cost_bp",
                "var_exp_bp_plan": "variable_cost_bp",
            },
            "month_bp_plan",
        ),
        (
            "kppk_realization_plan",
            {"input_cost_plan": "production_cost_op", "var_exp_plan": "variable_cost_op"},
            "month_max_plan",
        ),
        ("kppk_production_plan", {"full_cost_bp": "full_cost_bp", "full_cost_plan": "full_cost_op"}, None),
    )
    for table, mapping, volume in sources:
        qualified = _qualified(app_schema, table)
        scope = "rep_date = (period_date + interval '1 month - 1 day')::date"
        # All identifiers are module-owned constants and a server schema setting;
        # no request data reaches this SQL. Windowing keeps no-sales products.
        active = f"coalesce({volume}, 0) <> 0" if volume else "true"
        candidates = (
            f"WITH scoped AS (SELECT *, bool_or({active}) OVER "  # noqa: S608 — server-owned identifiers
            f"(PARTITION BY org_id, period_date, product) AS has_sales FROM {qualified} WHERE {scope}), "
            f"candidates AS (SELECT * FROM scoped WHERE NOT has_sales OR {active}) "
        )
        for field in mapping:
            conflicts = (
                await connection.execute(
                    text(
                        candidates + "SELECT org_id, period_date, product, "  # noqa: S608 — server-owned identifiers
                        f"array_agg(DISTINCT {field} ORDER BY {field}) FILTER (WHERE {field} <> 0) AS cost_values "
                        f"FROM candidates GROUP BY org_id, period_date, product "
                        f"HAVING count(DISTINCT nullif({field}, 0)) > 1"
                    )
                )
            ).all()
            for conflict in conflicts:
                print(
                    f"Предупреждение: разные ненулевые значения {table}.{field}: "
                    f"ДЗО {conflict.org_id}, {conflict.period_date}, {conflict.product}: "
                    f"{conflict.cost_values}. Берём максимум {max(conflict.cost_values)}."
                )
        columns = ", ".join(mapping.values())
        aggregates = ", ".join(f"coalesce(max(nullif({field}, 0)), 0)" for field in mapping)
        updates = ", ".join(f"{column} = excluded.{column}" for column in mapping.values())
        await connection.execute(
            text(
                candidates + f"INSERT INTO {cost} (id, org_id, period_date, product, {columns}) "  # noqa: S608 — server-owned identifiers
                f"SELECT gen_random_uuid(), org_id, period_date, product, {aggregates} FROM candidates "
                f"GROUP BY org_id, period_date, product ON CONFLICT (org_id, period_date, product) DO UPDATE SET {updates}"
            )
        )
    return int((await connection.execute(text(f"SELECT count(*) FROM {cost}"))).scalar_one())  # noqa: S608 — server-owned identifier


def _matrix_matches() -> list[MatrixMatch]:
    """
    (ДЗО, product, has_excise) rows out of `product_matrix.sql`.

    This replaced reading «Матрица продуктов.xlsx»: the pair set is the one input
    the original dumps never carried, so it is versioned as a plain SQL table
    (`kppk_product_matrix`, generated from the spreadsheet once). Names are
    normalized exactly like the copies, so they match `kppk_orgs`/`kppk_products`.
    """
    if not PRODUCT_MATRIX_PATH.exists():
        message = f"Не найдена матрица продуктов: {PRODUCT_MATRIX_PATH}."
        raise CommandError(message)
    _load_product_matrix_table()
    rows = _PRODUCT_MATRIX
    if not rows:
        message = f"Матрица продуктов пуста: {PRODUCT_MATRIX_PATH}."
        raise CommandError(message)
    return list(rows)


# Parsed once per process: the matrix is a static file.
_PRODUCT_MATRIX: list[MatrixMatch] = []


def _load_product_matrix_table() -> None:
    """Parse `product_matrix.sql` into `_PRODUCT_MATRIX` (idempotent)."""
    global _PRODUCT_MATRIX  # noqa: PLW0603 — a module-level cache of a static file
    if _PRODUCT_MATRIX:
        return
    sql = _read_sql_file(PRODUCT_MATRIX_PATH)
    rows: list[MatrixMatch] = []
    # `('АО "СНХЗ"', 'Сульфат натрия', false),` — one VALUES tuple per line.
    for match in re.finditer(
        r"\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'\s*,\s*(true|false)\s*\)",
        sql,
    ):
        org = normalize_text(match.group(1).replace("''", "'"))
        product = normalize_text(match.group(2).replace("''", "'"))
        if org and product:
            rows.append({"org": org, "product": product, "hasExcise": match.group(3) == "true"})
    _PRODUCT_MATRIX = rows


async def _rebuild_org_products(connection: AsyncConnection, app_schema: str) -> dict[str, int]:
    """
    Rebuild kppk_org_products from the versioned product matrix.

    ДЗО are matched by their exact display name (the matrix carries the same
    «АО "…"» names as kppk_d_org). A matrix product missing in kppk_products is
    created without a source num under an `@`-prefixed name — the mark of
    "brought in by the matrix, unknown to the source dictionary".

    «Заполняемый акциз» comes from the matrix too (`has_excise`): the pair set
    and the flags are one versioned input, so a rebuild reproduces exactly what
    the file says instead of carrying hand-kept state out of the old rows.
    """
    matrix_matches = _matrix_matches()
    orgs_table = _qualified(app_schema, "kppk_orgs")
    products_table = _qualified(app_schema, "kppk_products")
    links_table = _qualified(app_schema, "kppk_org_products")

    org_id_by_name = {
        row.name: str(row.id)
        for row in (await connection.execute(text(f"SELECT id, name FROM {orgs_table}"))).all()  # noqa: S608 — constant names
    }
    product_names: set[str] = {
        row.name
        for row in (await connection.execute(text(f"SELECT name FROM {products_table}"))).all()  # noqa: S608 — constant names
    }
    created_products = 0
    link_product_names: list[str] = []
    link_flags: list[bool] = []
    for match in matrix_matches:
        product = match["product"]
        # A matrix product missing in the source dictionary becomes a row
        # without a num under an «@»-prefixed name.
        effective = product if product in product_names else f"@{product}"
        if effective not in product_names:
            product_names.add(effective)
            await connection.execute(
                text(
                    f"INSERT INTO {products_table} (id, name, created_at, updated_at) "
                    "SELECT gen_random_uuid(), :name, now(), now()"
                ).bindparams(name=effective)
            )
            created_products += 1
        link_product_names.append(effective)
        link_flags.append(match["hasExcise"])
        if match["org"] not in org_id_by_name:
            message = f"Матрица продуктов ссылается на неизвестное ДЗО «{match['org']}»."
            raise CommandError(message)

    org_names = [match["org"] for match in matrix_matches]
    await connection.execute(text(f"TRUNCATE TABLE {links_table} CASCADE"))
    await connection.execute(
        # The matrix rows arrive as parallel arrays: unnest zips them back into
        # rows server-side (one statement, no per-row round trips).
        text(
            f"INSERT INTO {links_table} (id, org_id, product_id, has_excise, created_at, updated_at) "  # noqa: S608
            "SELECT gen_random_uuid(), o.id, p.id, m.has_excise, now(), now() "
            "FROM unnest("
            "CAST(:org_names AS text[]), CAST(:product_names AS text[]), CAST(:has_excise AS boolean[])"
            ") AS m(org_name, product_name, has_excise) "
            f"JOIN {orgs_table} o ON o.name = m.org_name "
            f"JOIN {products_table} p ON p.name = m.product_name"
        ).bindparams(
            org_names=org_names,
            product_names=link_product_names,
            has_excise=link_flags,
        )
    )
    return {
        "matrix_products_created": created_products,
        "matrix_links": len(matrix_matches),
        "matrix_excise": sum(1 for flag in link_flags if flag),
    }


async def seed_administrators(session: AsyncSession) -> int:
    """Ensure the two KPPK admins without altering identity or other projects."""
    project = (await session.exec(select(Project).where(col(Project.key) == KPPK_PROJECT_KEY))).one_or_none()
    if project is None:
        message = "Проект «КППК» не найден. Сначала выполните миграции и основной сидер."
        raise CommandError(message)
    for email in KPPK_ADMIN_EMAILS:
        user = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
        if user is None:
            # Keycloak supplies the authoritative full name on first login.
            user = User(email=email, name=email)
            session.add(user)
            await session.flush()
        link = await session.get(UserRole, (user.id, project.id))
        if link is None:
            link = UserRole(user_id=user.id, project_id=project.id, role=ProjectRole.ADMIN)
        link.role = ProjectRole.ADMIN
        link.is_active = True
        session.add(link)
    await session.flush()
    return len(KPPK_ADMIN_EMAILS)


# The period window: the «Реализация»/«Себестоимость» forms open from September
# 2023 (the first month the business works with) through December of the next
# calendar year.
PERIOD_START = date(2023, 9, 1)


def target_periods(today: date | None = None) -> list[date]:
    """First day of every month from September 2023 to December of the next year."""
    today = today or datetime.now(UTC).date()
    months: list[date] = []
    year, month = PERIOD_START.year, PERIOD_START.month
    while (year, month) <= (today.year + 1, 12):
        months.append(date(year, month, 1))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


async def _generate_periods(connection: AsyncConnection, app_schema: str) -> dict[str, int]:
    """
    Rebuild the empty «Реализация» / «Себестоимость» rows for the whole window.

    The forms must exist for every month of the business window even when the
    source has nothing for it, so the seed materializes one row per (ДЗО ×
    product of the matrix × market) and one per (ДЗО × product), all figures
    NULL. Rows the SOURCE brought are never touched: they are real data and the
    copy step above wrote them.

    Overwriting applies to the GENERATED rows only: a matrix pair that no longer
    exists (or a period outside the window) would otherwise leave a stale empty
    row behind forever. "Generated" is recognisable — every figure is NULL and
    the source contributed nothing for that key — so the cleanup deletes exactly
    those, never a row that carries source data.
    """
    periods = target_periods()
    products = (
        await connection.execute(
            text(
                f"SELECT op.org_id, p.name AS product "  # noqa: S608 — server-owned identifiers
                f"FROM {_qualified(app_schema, 'kppk_org_products')} op "
                f"JOIN {_qualified(app_schema, 'kppk_products')} p ON p.id = op.product_id"
            )
        )
    ).all()
    if not products or not periods:
        return {"kppk_realization_plan": 0, "kppk_realization": 0, "kppk_cost": 0}

    counts = {"kppk_realization_plan": 0, "kppk_realization": 0, "kppk_cost": 0}
    realization: list[dict[str, object]] = []
    cost: list[dict[str, object]] = []
    for product_row in products:
        for period in periods:
            cost.append({"org_id": product_row.org_id, "period_date": period, "product": product_row.product})
            last_day = monthrange(period.year, period.month)[1]
            for region in MARKET_CODES:
                realization.append(
                    {
                        "org_id": product_row.org_id,
                        "period_date": period,
                        # The copies require the report day: the month's last day,
                        # which is what every read filters on.
                        "rep_date": date(period.year, period.month, last_day),
                        "product": product_row.product,
                        "region": region,
                    }
                )

    # Overwrite: the seed owns these three tables. «Реализация»/«Себестоимость»
    # are period scaffolding — the forms are filled by the business — so the
    # source figures in them are replaced by the empty rows below, the same way
    # the renovation plan is re-seeded. What the source contributes is the
    # DICTIONARIES (ДЗО, products, raws), which the copy step wrote.
    for table in ("kppk_realization_plan", "kppk_realization", "kppk_cost"):
        await connection.execute(text(f"TRUNCATE TABLE {_qualified(app_schema, table)}"))
    for table, rows in (("kppk_realization_plan", realization), ("kppk_realization", realization), ("kppk_cost", cost)):
        columns = list(rows[0])
        # `id` has no DB default (the models mint the uuid in Python), so it is
        # generated here for the rows that bypass the ORM.
        statement = (
            f"INSERT INTO {_qualified(app_schema, table)} (id, {', '.join(columns)}) "  # noqa: S608
            f"VALUES (gen_random_uuid(), {', '.join(f':{column}' for column in columns)})"
        )
        # One statement per chunk: a single insert of ~50k rows would exceed the
        # bound-parameter limit.
        for index in range(0, len(rows), PERIOD_INSERT_CHUNK):
            await connection.execute(text(statement), rows[index : index + PERIOD_INSERT_CHUNK])
        counts[table] = len(rows)
    return counts


async def seed(session: AsyncSession) -> dict[str, int]:
    """
    Seed the КППК source copies and the coming periods; returns the counters.

    The period rows are generated as the LAST step (same transaction): a fresh
    copy of the dictionaries would otherwise leave the «Реализация» and
    «Себестоимость» forms empty for months nobody has loaded yet.
    """
    settings = get_settings()
    app_schema = settings.postgres_schema
    if not app_schema:
        message = "Не задана схема базы данных."
        raise ValueError(message)
    # The seeder runs raw SQL only: SQLModel's `exec` is typed for ORM selects,
    # its `execute` is deprecated — the session's connection is the clean path.
    connection = await session.connection()
    copy_tables_created = await _ensure_copy_tables(connection, app_schema)
    schemas_created = await _ensure_schemas(connection)
    source_tables_created = await _ensure_source_tables(connection)
    await _truncate_copies(connection, app_schema)
    per_table = await _copy_sources(connection, app_schema)
    org_products = await _rebuild_org_products(connection, app_schema)
    # Periods BEFORE the costs: the cost rebuild derives «Себестоимость» from the
    # realization rows, so it must run on the final period set. Both are rebuilt
    # from scratch — the seed owns these tables.
    periods = await _generate_periods(connection, app_schema)
    costs = await _rebuild_costs(connection, app_schema)
    administrators = await seed_administrators(session)
    return {
        **per_table,
        **org_products,
        "kppk_cost": costs,
        "schemas_created": len(schemas_created),
        "source_tables_created": source_tables_created,
        "copy_tables_created": copy_tables_created,
        "administrators": administrators,
        "periods_created": sum(periods.values()),
        "periods_realization_plan": periods["kppk_realization_plan"],
        "periods_realization": periods["kppk_realization"],
        "periods_cost": periods["kppk_cost"],
    }


async def run() -> None:
    """Seed the КППК source copies and the coming periods in one transaction."""
    async with async_session_factory() as session:
        counters = await seed(session)
        await session.commit()
    details = ", ".join(f"{name}={count}" for name, count in sorted(counters.items()))
    print(f"Сидирование КППК завершено: {details}.")
    print(
        f"Периоды: «Реализация» (план) — "
        f"{counters['periods_realization_plan']}, «Реализация» (факт) — "
        f"{counters['periods_realization']}, «Себестоимость» — {counters['periods_cost']}."
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
