"""
Diagnose a КППК seed failure: which dictionary fails, and on which values.

The seed copies `raw.kppk_f_*` into `kppk_f_*` and resolves four things per row:
the period (`raw.kppk_d_period`), the ДЗО (`kppk_orgs.num`), the product and —
for the realization copies — the market. When any of them does not resolve, the
whole run aborts. This prints, per source and per dictionary, how many rows fail
and a sample of the offending source values, so the fix is obvious.

Self-contained on purpose: it imports NOTHING from `seed_kppk`, so it runs on a
checkout whose seeder predates the per-dictionary error. Only the app's own
config/database modules and the source schema constant are reused.

Read-only: nothing is written.

Usage (on the box, with the app's own settings):

    uv run --directory back python -m src.scripts.diagnose_kppk_seed
"""

# ruff: noqa: S608 — every interpolated identifier is server-owned (schema and
# table names are constants below); values are bound params.

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from src.config import get_settings
from src.database import async_engine
from src.kppk.constants import KPPK_SOURCE_SCHEMA_RAW

# The realization copies carry a market; the production plan does not.
_WITH_MARKET = ("kppk_f_realization", "kppk_f_realization_plan")
_WITHOUT_MARKET = ("kppk_f_production_plan",)

# One dictionary check: kind, table, join key, source expression, a label for the
# operator, and the JOIN condition. The source expression MUST mirror
# `_normalized_sql` in the seeder — a bare `btrim` would not see the
# non-breaking space or a doubled internal space, and the check would disagree
# with the seed it is diagnosing. The product condition mirrors the seeder's
# match too: a name OR its «@» form (products the source dictionary never
# carried), so the report does not flag a product the seed would resolve.
_PRODUCT_EXPR = (
    """replace(regexp_replace(btrim(replace(s."product", chr(160), ' ')), '[[:space:]]+', ' ', 'g'), ';', ',')"""
)
_REGION_EXPR = (
    """replace(regexp_replace(btrim(replace(s."region", chr(160), ' ')), '[[:space:]]+', ' ', 'g'), ';', ',')"""
)

_DICTIONARIES: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "period",
        "raw.kppk_d_period",
        "id",
        "s.period_id",
        "raw.kppk_d_period.id = raw.*.period_id",
        "d{index}.id = s.period_id",
    ),
    (
        "org",
        "app.kppk_orgs",
        "num",
        "s.org_id",
        "kppk_orgs.num = raw.*.org_id",
        "d{index}.num = s.org_id",
    ),
    (
        "product",
        "app.kppk_products",
        "name",
        _PRODUCT_EXPR,
        "kppk_products.name = raw.*.product (или «@»-форма)",
        "d{index}.name IN (" + _PRODUCT_EXPR + ", '@' || " + _PRODUCT_EXPR + ")",
    ),
    (
        "market",
        "app.kppk_markets",
        "code",
        _REGION_EXPR,
        "kppk_markets.code = raw.*.region",
        "d{index}.code = " + _REGION_EXPR,
    ),
)


def _qualified(schema: str, name: str) -> str:
    """Quote a server-owned table name; `raw` lives outside the app schema."""
    return f'"{schema}"."{name}"'


def _table(schema: str, name: str) -> str:
    """Resolve the `raw.` prefix to the source schema, everything else to `schema`."""
    owner, _, table = name.partition(".")
    return _qualified(KPPK_SOURCE_SCHEMA_RAW if owner == "raw" else schema, table)


def _checks(has_market: bool) -> tuple[tuple[str, str, str, str, str, str], ...]:
    return tuple(check for check in _DICTIONARIES if has_market or check[0] != "market")


async def _count(connection: AsyncConnection, sql: str, **params: object) -> int:
    return int((await connection.execute(text(sql), params)).scalar_one())


async def _dictionary_sizes(connection: AsyncConnection, schema: str) -> None:
    print("\n=== справочники ===")
    for label, name in (
        ("raw.kppk_d_period", "raw.kppk_d_period"),
        ("kppk_orgs", "app.kppk_orgs"),
        ("kppk_products", "app.kppk_products"),
        ("kppk_markets", "app.kppk_markets"),
    ):
        owner, _, table = name.partition(".")
        target = KPPK_SOURCE_SCHEMA_RAW if owner == "raw" else schema
        try:
            # A savepoint keeps a missing table from aborting the whole
            # transaction: without it every later query would fail with
            # "current transaction is aborted", hiding the real finding.
            async with connection.begin_nested():
                size = await _count(connection, f'SELECT count(*) FROM "{target}"."{table}"')
        except Exception as error:  # noqa: BLE001 — a missing table IS the finding
            print(f"  {label:20} ОШИБКА: {type(error).__name__}: {error}")
            continue
        print(f"  {label:20} {size}")


async def _report_source(
    connection: AsyncConnection,
    schema: str,
    source: str,
    *,
    has_market: bool,
) -> None:
    table = _table(schema, f"raw.{source}")
    total = await _count(connection, f"SELECT count(*) FROM {table}")
    print(f"\n=== {source}: {total} строк ===")
    checks = _checks(has_market)
    # Every join is kept in every count: a row is counted for ONE dictionary,
    # whatever the other three resolve to.
    joins = " ".join(
        f"LEFT JOIN {_table(schema, target)} d{index} ON {condition.format(index=index)}"
        for index, (_, target, _key, _expression, _label, condition) in enumerate(checks)
    )
    failed = False
    for index, (kind, _, key, expression, label, _condition) in enumerate(checks):
        missing = await _count(
            connection,
            f"SELECT count(*) FROM {table} s {joins} WHERE d{index}.{key} IS NULL",
        )
        if not missing:
            print(f"  {kind:8} OK")
            continue
        failed = True
        print(f"  {kind:8} НЕ НАЙДЕНО в {missing} строках — сверьте {label}:")
        samples = (
            (
                await connection.execute(
                    text(f"SELECT DISTINCT {expression} FROM {table} s {joins} WHERE d{index}.{key} IS NULL LIMIT 10")
                )
            )
            .scalars()
            .all()
        )
        for sample in samples:
            print(f"             {sample!r}")
    if not failed:
        print("  все справочники сопоставлены — источник сидируется")


async def _run() -> None:
    schema = get_settings().postgres_schema or "rc"
    print(f"schema: {schema}")
    async with async_engine.connect() as connection:
        await _dictionary_sizes(connection, schema)
        for source in _WITH_MARKET:
            await _report_source(connection, schema, source, has_market=True)
        for source in _WITHOUT_MARKET:
            await _report_source(connection, schema, source, has_market=False)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
