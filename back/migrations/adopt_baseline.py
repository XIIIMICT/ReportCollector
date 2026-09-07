"""Validate an existing current schema before replacing its legacy revision marker."""

import asyncio
from pathlib import Path
from typing import cast

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Enum, inspect, text
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

from src import models as _models  # noqa: F401
from src.collab import models as _collab_models  # noqa: F401
from src.database import async_engine

BASELINE = "0001_baseline"
LEGACY_HEAD = "0015_plan_row_col_218"


def compare_adoption_default(
    _context: MigrationContext,
    inspected_column: Column,
    _metadata_column: Column,
    inspected_default: str | None,
    _metadata_default: object,
    _rendered_metadata_default: str | None,
) -> bool | None:
    """Allow the known pre-0003 variant; compare every other default normally."""
    # Legacy 0008 installed now(); create_all and the frozen baseline omitted it.
    # 0003 converges both variants without modifying existing timestamps.
    if (
        inspected_column.table.name == "renovation_reference_values"
        and inspected_column.name == "created_at"
        and inspected_default is None
    ):
        return False
    return None


def adopt(connection: Connection) -> None:
    """Only the known previous head and a matching schema can be adopted."""
    directory = Path(__file__).resolve().parent
    config = Config(str(directory.parent / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    if ScriptDirectory.from_config(config).get_revision(BASELINE) is None:
        message = "Переход выполняется только из релиза с единой baseline-миграцией."
        raise ValueError(message)
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        message = "Нет alembic_version. Для новой базы используйте alembic upgrade head."
        raise ValueError(message)
    connection.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
    versions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if versions not in ([LEGACY_HEAD], [BASELINE]):
        message = f"Неподдерживаемая ревизия базы данных: {versions}."
        raise ValueError(message)

    def include_object(_obj: object, name: str | None, type_: str, reflected: bool, _compare_to: object) -> bool:
        # Obsolete journal tables may remain in legacy installations. Preserve them.
        if type_ == "table" and name in {"collab_edit_stacks", "collab_cell_states", "collab_outbox"}:
            return False
        return not (type_ == "table" and reflected and name in {"rm_cell_state", "rm_edit_log"})

    diffs = compare_metadata(
        MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": compare_adoption_default,
                "include_object": include_object,
            },
        ),
        SQLModel.metadata,
    )
    if diffs:
        message = f"Схема отличается от актуальных моделей; переход отменён: {diffs!r}"
        raise ValueError(message)
    # Alembic does not detect enum label or primary-key changes automatically.
    enums = {item["name"]: item["labels"] for item in cast("PGInspector", inspector).get_enums()}
    for table in SQLModel.metadata.sorted_tables:
        if table.name in {"collab_edit_stacks", "collab_cell_states", "collab_outbox"}:
            continue
        expected_pk = [column.name for column in table.primary_key.columns]
        if inspector.get_pk_constraint(table.name)["constrained_columns"] != expected_pk:
            message = f"Схема отличается: первичный ключ {table.name}."
            raise ValueError(message)
        for column in table.columns:
            if isinstance(column.type, Enum) and enums.get(column.type.name) != column.type.enums:
                message = f"Схема отличается: значения enum {column.type.name}."
                raise ValueError(message)
    config.attributes["connection"] = connection
    command.stamp(config, BASELINE, purge=True)


async def main() -> None:
    try:
        async with async_engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '30s'"))
            await connection.run_sync(adopt)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
