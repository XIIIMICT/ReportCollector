"""Upgrade fresh, current and validated legacy installations in one transaction."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

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
    target_schema = get_settings().postgres_schema
    # `just seed` recreates the models but not the marker table, so the marker
    # may sit in another schema (or nowhere) — both are handled below.
    marker_schema = _marker_schema(connection)
    versions: list[str] = []
    if marker_schema is not None:
        qualified = connection.dialect.identifier_preparer.quote_schema(marker_schema)
        connection.execute(text(f"LOCK TABLE {qualified}.alembic_version IN ACCESS EXCLUSIVE MODE"))
        versions = list(connection.execute(text(f"SELECT version_num FROM {qualified}.alembic_version")).scalars())  # noqa: S608
        if versions == [LEGACY_HEAD]:
            adopt(connection)
        _adopt_seeded_kppk_calendar_schema(connection, versions)
    head = ScriptDirectory.from_config(config).get_current_head() or ""
    if _seeded_schema_is_current(connection, target_schema):
        # `just seed` built the CURRENT models, there is no chain to replay;
        # stamping (and creating) the marker is all that is left.
        _write_marker(connection, target_schema, head)
    elif versions and versions != [LEGACY_HEAD] and marker_schema != target_schema:
        # The real marker lives elsewhere (a test schema left behind by a test
        # run): the target must not be replayed as an empty history.
        _write_marker(connection, target_schema, head)
    command.upgrade(config, "head")


# `just seed` recreates the model tables it imports; the durable queue lives in
# `src.common.job_models`, which the seed never registers — main does.
_SEED_EXEMPT_TABLE = "data_jobs"


def _marker_schema(connection: Connection) -> str | None:
    """Find the schema holding `alembic_version`: the current one, else the configured one."""
    if "alembic_version" in inspect(connection).get_table_names():
        return connection.execute(text("SELECT current_schema()")).scalar_one()
    configured = get_settings().postgres_schema
    if inspect(connection).has_table("alembic_version", schema=configured):
        return configured
    return None


def _seeded_schema_is_current(connection: Connection, schema: str | None) -> bool:
    """
    Whether `schema` was built from the CURRENT models (`just seed`).

    Only the closed shape is accepted: every table, column and enum TYPE the
    models declare, plus the newest addition the seed DOES install (the
    defect-number index of 0031) — so a schema an older seed left behind goes
    through the migration chain instead of being stamped complete. `data_jobs`
    is exempt: `just seed` never registers that queue model (`src.main` does),
    so it cannot be part of the seeded shape.
    """
    if schema is None:
        return False
    declared_enums = {
        column.type.name
        for table in SQLModel.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, SAEnum)
    }
    existing_enums = set(
        connection.execute(
            text(
                "SELECT DISTINCT t.typname FROM pg_type t "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = :schema AND t.typtype = 'e'"
            ),
            {"schema": schema},
        ).scalars()
    )
    if not declared_enums <= existing_enums:
        return False
    inspector = inspect(connection)
    for table in SQLModel.metadata.sorted_tables:
        if table.name == _SEED_EXEMPT_TABLE:
            continue
        if not inspector.has_table(table.name, schema=schema):
            return False
        actual = {column["name"] for column in inspector.get_columns(table.name, schema=schema)}
        if not {column.name for column in table.columns} <= actual:
            return False
    indexes = {index["name"] for index in inspector.get_indexes("r_plan_rows", schema=schema)}
    return "ix_r_plan_rows_plan_col_023" in indexes


def _write_marker(connection: Connection, schema: str | None, head: str) -> None:
    """Point the migration marker of `schema` at `head` (creating it if needed)."""
    if schema is None:
        return
    qualified = connection.dialect.identifier_preparer.quote_schema(schema)
    exists = connection.execute(text(f"SELECT to_regclass('{qualified}.alembic_version')")).scalar_one()
    if exists is None:
        connection.execute(text(f"CREATE TABLE {qualified}.alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text(f"INSERT INTO {qualified}.alembic_version (version_num) VALUES (:head)"),  # noqa: S608
            {"head": head},
        )
        return
    connection.execute(
        text(f"UPDATE {qualified}.alembic_version SET version_num=:head"),  # noqa: S608
        {"head": head},
    )


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
            # `async_engine`'s search_path option applies to pooled connections;
            # the migration connection must resolve the app's objects explicitly,
            # or every lookup lands in `public`.
            await connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            await connection.run_sync(upgrade)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
