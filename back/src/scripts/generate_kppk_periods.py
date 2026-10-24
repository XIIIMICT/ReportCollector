"""
Generate the missing «Реализация» / «Себестоимость» rows for the coming periods.

ReportCollector exists so that a business user can FILL these forms, not only
read what the source dumps happened to contain. A period that nobody has loaded
yet therefore must still show the grid: one row per (ДЗО × продукт из матрицы ×
рынок) for «Реализация», and one row per (ДЗО × продукт) for «Себестоимость»,
every numeric cell left empty (NULL — the form shows it as unfilled).

Scope: every calendar month from January of the CURRENT year through December
of the NEXT one. Existing rows are never touched — only combinations that are
missing get inserted, so the command is idempotent and safe to re-run after a
`seed-kppk`.

Which products: the «Продуктовые позиции» matrix (`kppk_org_products`) — the set
the asset actually offers. The copies key products by NAME (not by id), so the
dictionary name is what goes into `product`.

Usage:
  uv run --directory back -m src.scripts.generate_kppk_periods
  uv run --directory back -m src.scripts.generate_kppk_periods --dry-run
"""

import argparse
import asyncio
from calendar import monthrange
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.database import async_session_factory
from src.kppk.constants import MARKET_CODES

TABLES = ("kppk_realization_plan", "kppk_realization", "kppk_cost")
# Bound-parameter ceiling per statement: a single 10k-row insert is rejected.
INSERT_CHUNK = 1000


def target_periods(today: date | None = None) -> list[date]:
    """First day of every month from January of the current year to next December."""
    today = today or datetime.now(UTC).date()
    return [date(year, month, 1) for year in (today.year, today.year + 1) for month in range(1, 13)]


def _qualified(name: str) -> str:
    """
    Quote a table name for the CURRENT search path.

    The schema is left unqualified on purpose: the session's `search_path`
    decides it (`rc` in the app, `rc_test*` under pytest), so the command works
    in the test schema and in production without a second switch. The name is a
    literal from this module, never user input — hence the S608 waivers at the
    call sites.
    """
    return f'"{name}"'


def _key(table: str, row: dict[str, object]) -> tuple[object, ...]:
    """Return the natural key of a copy row (what makes a row "already there")."""
    if table == "kppk_cost":
        return (row["org_id"], row["period_date"], row["product"])
    return (row["org_id"], row["period_date"], row["product"], row["region"])


async def _existing(session: AsyncSession, table: str) -> set[tuple[object, ...]]:
    """Return the natural keys already present in a copy."""
    connection = await session.connection()
    result = await connection.execute(text(f"SELECT * FROM {_qualified(table)}"))  # noqa: S608 — server-owned identifier
    return {_key(table, dict(row)) for row in result.mappings()}


async def _asset_products(session: AsyncSession) -> list[tuple[object, str]]:
    """Return the (org id, product name) pairs of the «Продуктовые позиции» matrix."""
    connection = await session.connection()
    result = await connection.execute(
        text(
            f"SELECT op.org_id, p.name AS product "  # noqa: S608 — server-owned identifiers
            f"FROM {_qualified('kppk_org_products')} op "
            f"JOIN {_qualified('kppk_products')} p ON p.id = op.product_id "
            f"ORDER BY p.product_num NULLS LAST, p.name"
        )
    )
    return [(row.org_id, row.product) for row in result]


def _candidates(table: str, products: list[tuple[object, str]], periods: list[date]) -> list[dict[str, object]]:
    """Return every (asset × product × period [× market]) combination the table needs."""
    rows: list[dict[str, object]] = []
    for org_id, product in products:
        for period in periods:
            base = {"org_id": org_id, "period_date": period, "product": product}
            if table == "kppk_cost":
                # «Себестоимость» has no market dimension: UNIQUE (org, month, product).
                rows.append(dict(base))
                continue
            last_day = monthrange(period.year, period.month)[1]
            for region in MARKET_CODES:
                rows.append(
                    {
                        **base,
                        # The copies require the report day: the month's last day,
                        # which is what every read filters on.
                        "rep_date": date(period.year, period.month, last_day),
                        "region": region,
                    }
                )
    return rows


async def _insert(session: AsyncSession, table: str, rows: list[dict[str, object]]) -> None:
    """Insert the rows that are still missing at write time."""
    columns = list(rows[0])
    # `id` has no DB default (the models mint the uuid in Python), so it is
    # generated here for the rows that bypass the ORM.
    column_list = "id, " + ", ".join(columns)
    values = "gen_random_uuid(), " + ", ".join(f":{column}" for column in columns)
    # The realization copies have NO unique key on their natural columns (only
    # the uuid PK), so `ON CONFLICT` cannot deduplicate them — the candidate
    # list was already diffed against the table. «Себестоимость» does have
    # `uq_kppk_cost_scope`, which stays as the final guard against a race.
    conflict = "ON CONFLICT (org_id, period_date, product) DO NOTHING" if table == "kppk_cost" else ""
    statement = f"INSERT INTO {_qualified(table)} ({column_list}) VALUES ({values}) {conflict}"  # noqa: S608
    # Batch: one statement per chunk keeps the bound-parameter count sane.
    connection = await session.connection()
    for index in range(0, len(rows), INSERT_CHUNK):
        await connection.execute(text(statement), rows[index : index + INSERT_CHUNK])


async def generate(dry_run: bool = False, session: AsyncSession | None = None) -> dict[str, int]:
    """
    Insert the missing rows; returns the per-table counters.

    `session` lets a caller (a test) reuse its own transaction; by default the
    command opens one and commits it.
    """
    periods = target_periods()
    if session is not None:
        return await _generate_in(session, periods=periods, dry_run=dry_run)
    async with async_session_factory() as own:
        counts = await _generate_in(own, periods=periods, dry_run=dry_run)
        if dry_run:
            await own.rollback()
        else:
            await own.commit()
    return counts


async def _generate_in(session: AsyncSession, *, periods: list[date], dry_run: bool) -> dict[str, int]:
    """Do the work inside one session (no commit — the caller owns the transaction)."""
    counts: dict[str, int] = {}
    products = await _asset_products(session)
    for table in TABLES:
        existing = await _existing(session, table)
        missing = [row for row in _candidates(table, products, periods) if _key(table, row) not in existing]
        counts[table] = len(missing)
        if not dry_run and missing:
            await _insert(session, table, missing)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Только показать, сколько строк будет добавлено.")
    args = parser.parse_args()
    counts = asyncio.run(generate(dry_run=args.dry_run))
    periods = target_periods()
    action = "будет добавлено" if args.dry_run else "добавлено"
    print(
        f"Периоды: {periods[0]:%m.%Y}–{periods[-1]:%m.%Y}. {action}: "
        f"«Реализация» (план) — {counts['kppk_realization_plan']}, "
        f"«Реализация» (факт) — {counts['kppk_realization']}, "
        f"«Себестоимость» — {counts['kppk_cost']}."
    )


if __name__ == "__main__":
    main()
