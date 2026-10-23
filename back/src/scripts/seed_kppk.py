"""
Seed the «КППК» copies of the source tables without touching the other projects.

The source tables ship as SQL dumps in `docs/kppk/` (`kppk_*` tables + the
`dict.markets` dictionary; structure in `ddl.sql`). The seeder:

1. creates the `raw` / `dict` schemas and the source tables **only when
   missing** — on prod installations they already exist and are never touched;
2. restores missing application tables from the current models, then re-copies
   source data on every run; existing tables still evolve through migrations;
3. rebuilds «Продуктовые позиции» (`kppk_org_products`) from
   «Матрица продуктов.xlsx»: products and ДЗО are matched by exact display
   name, and a matrix product missing in the source dictionary is created
   without a num under an `@`-prefixed name.

Re-copying on every run keeps the behavior deterministic: any manual dictionary
edit is reconciled from the source at the next seed. The kppk project row and
its administrator links are ensured by `seed_administrators` (also called by
the full seeder). Other projects' memberships and grants are left untouched.

Usage: uv run --directory back -m src.scripts.seed_kppk
"""

import asyncio
import re
from pathlib import Path
from typing import Final, TypedDict

import openpyxl
from sqlalchemy import CursorResult, MetaData, func, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.config import get_settings
from src.database import async_session_factory
from src.kppk import models as _kppk_models  # noqa: F401 — register current application table definitions
from src.kppk.constants import KPPK_PROJECT_KEY, KPPK_SOURCE_SCHEMA_DICT, KPPK_SOURCE_SCHEMA_RAW, KPPK_SOURCE_TABLES

# docs/kppk/ of the repository (`back/src/scripts/seed_kppk.py` → 3 levels up).
KPPK_DOCS_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "docs" / "kppk"
MATRIX_XLSX_NAME: Final[str] = "Матрица продуктов.xlsx"
MATCH_COLUMN_VALUE: Final[str] = "да"
KPPK_ADMIN_EMAILS: Final[tuple[str, ...]] = ("evtushenko.sn@ruschem.ru", "lavrova.mg@ruschem.ru")
# Only the four dictionary copies get audit stamps; the fact/plan copies stay
# verbatim (the migration does not create audit columns for them).
STAMPED_COPY_TABLES: Final[frozenset[str]] = frozenset({"kppk_d_org", "kppk_d_product_seq", "kppk_d_raw_seq"})

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
    """One matched «Матрица продуктов» row: ДЗО id + product."""

    org: str
    product: str


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


def _is_table_dump(name: str, table: str) -> bool:
    """Return True when a docs/kppk file name is `<table>_<timestamp>.sql`."""
    return re.fullmatch(rf"{re.escape(table)}_[0-9]+\.sql", name) is not None


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


async def _run_file(connection: AsyncConnection, path: Path) -> None:
    """Execute a dump file statement by statement."""
    for statement in split_sql_statements(_read_sql_file(path)):
        await connection.execute(text(statement))


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
    """Create the missing raw/dict schemas; returns the created names."""
    created: list[str] = []
    for schema in (KPPK_SOURCE_SCHEMA_RAW, KPPK_SOURCE_SCHEMA_DICT):
        if not await _schema_exists(connection, schema):
            await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            created.append(schema)
    return created


async def _ensure_source_tables(connection: AsyncConnection) -> int:
    """Create + fill the missing source tables from docs/kppk; returns the count."""
    created = 0
    ddl_path = KPPK_DOCS_DIR / "ddl.sql"
    if not ddl_path.exists():
        message = f"Не найден файл структуры исходных таблиц: {ddl_path}."
        raise CommandError(message)
    for statement in split_sql_statements(_read_sql_file(ddl_path)):
        target = _statement_target(statement)
        if target is None or await _table_exists(connection, *target):
            continue
        schema, table = target
        await connection.execute(text(statement))
        # The dump of the same table holds both the structure and the data;
        # the stem match stays exact — `kppk_f_realization` must not swallow
        # the `kppk_f_realization_plan` dumps.
        dumps = sorted(path for path in KPPK_DOCS_DIR.glob(f"{table}_*.sql") if _is_table_dump(path.name, table))
        if not dumps:
            message = f"Не найден дамп для таблицы {schema}.{table}: docs/kppk/{table}_*.sql."
            raise CommandError(message)
        await _run_file(connection, dumps[-1])
        created += 1
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
        source_columns = ", ".join(column for column, _ in columns)
        # The four dictionaries carry audit stamps; the fact/plan copies stay
        # verbatim without them (the migration does not create those columns).
        stamped = source in STAMPED_COPY_TABLES
        stamp_columns = ", created_at, updated_at" if stamped else ""
        stamp_values = ", now(), now()" if stamped else ""
        if source in {"kppk_f_realization", "kppk_f_realization_plan", "kppk_f_production_plan"}:
            copied = [column for column, _ in columns if column not in {"org_id", "period_id"}]
            target_columns = ", ".join(copied)
            zero_fields = {"fact", "month_bp_plan", "month_max_plan", "excise_bp", "excise_max_plan", "excise_fact"}
            source_columns = ", ".join(
                f"coalesce(s.{column}, 0)" if column in zero_fields else f"s.{column}" for column in copied
            )
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
                    f"FROM {_qualified(KPPK_SOURCE_SCHEMA_RAW, source)}"
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
    """(ДЗО, product) pairs out of «Матрица продуктов.xlsx» («да» cells)."""
    matrix = KPPK_DOCS_DIR / MATRIX_XLSX_NAME
    if not matrix.exists():
        message = f"Не найдена матрица продуктов: {matrix}."
        raise CommandError(message)
    sheet = openpyxl.load_workbook(matrix, data_only=True).worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        message = f"Матрица продуктов пуста: {matrix}."
        raise CommandError(message)
    orgs = [str(cell).strip() for cell in rows[0][1:] if cell is not None]
    if not orgs:
        message = f"В матрице продуктов нет колонок ДЗО: {matrix}."
        raise CommandError(message)
    matches: list[MatrixMatch] = []
    for row in rows[1:]:
        product = str(row[0]).strip() if row[0] is not None else ""
        if not product:
            continue
        for org_name, cell in zip(orgs, row[1 : len(orgs) + 1], strict=False):
            if str(cell).strip() == MATCH_COLUMN_VALUE:
                matches.append({"org": org_name, "product": product})
    return matches


async def _rebuild_org_products(connection: AsyncConnection, app_schema: str) -> dict[str, int]:
    """
    Rebuild kppk_org_products from «Матрица продуктов.xlsx».

    ДЗО are matched by their exact display name (the matrix columns carry the
    same «АО "…"» names as kppk_d_org). A matrix product missing in
    kppk_products is created without a source num under an `@`-prefixed name —
    the mark of "brought in by the matrix, unknown to the source dictionary".
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
        if match["org"] not in org_id_by_name:
            message = f"Матрица продуктов ссылается на неизвестное ДЗО «{match['org']}»."
            raise CommandError(message)

    # «Заполняемый акциз» is kept per product × ДЗО position, and the matrix is
    # the source of the PAIRS only: the flags are hand-kept, so the rebuild
    # carries them over instead of wiping them with the rows.
    excise_pairs = {
        (row.org_name, row.product_name)
        for row in (
            await connection.execute(
                text(
                    f"SELECT o.name AS org_name, p.name AS product_name "  # noqa: S608 — constant names
                    f"FROM {links_table} op "
                    f"JOIN {orgs_table} o ON o.id = op.org_id "
                    f"JOIN {products_table} p ON p.id = op.product_id "
                    "WHERE op.has_excise"
                )
            )
        ).all()
    }
    org_names = [match["org"] for match in matrix_matches]
    await connection.execute(text(f"TRUNCATE TABLE {links_table} CASCADE"))
    await connection.execute(
        # The matrix pairs arrive as two parallel arrays: unnest zips them back
        # into rows server-side (one statement, no per-row round trips).
        text(
            f"INSERT INTO {links_table} (id, org_id, product_id, created_at, updated_at) "  # noqa: S608
            "SELECT gen_random_uuid(), o.id, p.id, now(), now() "
            "FROM unnest(CAST(:org_names AS text[]), CAST(:product_names AS text[])) AS m(org_name, product_name) "
            f"JOIN {orgs_table} o ON o.name = m.org_name "
            f"JOIN {products_table} p ON p.name = m.product_name"
        ).bindparams(
            org_names=org_names,
            product_names=link_product_names,
        )
    )
    # Restore the hand-kept flags on the pairs that still exist in the matrix.
    for org_name, product_name in sorted(excise_pairs):
        await connection.execute(
            text(
                f"UPDATE {links_table} op SET has_excise = true "  # noqa: S608 — constant names
                f"FROM {orgs_table} o, {products_table} p "
                "WHERE o.id = op.org_id AND p.id = op.product_id "
                "AND o.name = :org_name AND p.name = :product_name"
            ),
            {"org_name": org_name, "product_name": product_name},
        )
    return {"matrix_products_created": created_products, "matrix_links": len(matrix_matches)}


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


async def seed(session: AsyncSession) -> dict[str, int]:
    """Seed the КППК source copies; returns the counters printed by `run`."""
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
    }


async def run() -> None:
    """Seed the КППК source copies in one transaction."""
    async with async_session_factory() as session:
        counters = await seed(session)
        await session.commit()
    details = ", ".join(f"{name}={count}" for name, count in sorted(counters.items()))
    print(f"Сидирование КППК завершено: {details}.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
