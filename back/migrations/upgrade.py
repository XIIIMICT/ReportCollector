"""Upgrade fresh, current and validated legacy installations in one transaction."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from migrations.adopt_baseline import LEGACY_HEAD, adopt, prune_legacy_revision_files
from src.config import get_settings
from src.database import async_engine


def upgrade(connection: Connection) -> None:
    """Adopt only the known old head; let Alembic reject other unknown revisions."""
    directory = Path(__file__).resolve().parent
    # Prune the retired chain's revision files before anything reads the versions
    # directory: a checkout updated in place keeps them as untracked leftovers and
    # Alembic would see several heads.
    prune_legacy_revision_files(directory)
    config = Config(str(directory.parent / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    config.attributes["connection"] = connection
    if "alembic_version" in inspect(connection).get_table_names():
        connection.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
        versions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if versions == [LEGACY_HEAD]:
            adopt(connection)
        _adopt_seeded_kppk_calendar_schema(connection, versions)
    command.upgrade(config, "head")


def _adopt_seeded_kppk_calendar_schema(connection: Connection, versions: list[str]) -> None:
    """
    Stamp 0012 when seed-kppk already restored its post-0012 schema.

    The KPPK-only seed can recreate current application tables after an
    operator removed them. If the migration marker still says 0011, replaying
    0012 would treat UUID/date columns as the old integer-id schema and fail.
    This closed shape check only skips the transformation when all post-0012
    tables/columns are present; incomplete schemas still fail normally.
    """
    if versions != ["0011_kppk_realization_buyout"]:
        return
    inspector = inspect(connection)
    required = {
        "kppk_orgs": {"id", "num"},
        "kppk_realization": {"id", "org_id", "period_date"},
        "kppk_realization_plan": {"id", "org_id", "period_date"},
        "kppk_cost": {"id", "org_id", "period_date"},
        "kppk_production_plan": {"id", "org_id", "period_date"},
    }
    for table, columns in required.items():
        if not inspector.has_table(table):
            return
        actual = {column["name"] for column in inspector.get_columns(table)}
        if not columns <= actual:
            return
    if inspector.has_table("kppk_periods"):
        return
    connection.execute(text("UPDATE alembic_version SET version_num='0012_kppk_calendar_cost'"))


async def main() -> None:
    settings = get_settings()
    try:
        if not settings.postgres_schema:
            message = "Не задана схема базы данных."
            raise ValueError(message)
        async with async_engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '30s'"))
            schema = connection.dialect.identifier_preparer.quote_schema(settings.postgres_schema)
            await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await connection.run_sync(upgrade)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
