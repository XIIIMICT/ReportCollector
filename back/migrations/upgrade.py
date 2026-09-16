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
    command.upgrade(config, "head")


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
