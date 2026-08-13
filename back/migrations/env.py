"""
Alembic окружение: асинхронные миграции поверх настроек ReportCollector.

URL и search_path берутся из src.config (POSTGRES_* / DATABASE_URL), а не из
alembic.ini. Схема приложения (rc) задаётся через server_settings, поэтому
миграции создают и изменяют объекты именно в ней.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from src.auth import models as auth_models  # noqa: F401
from src.collab import models as collab_models  # noqa: F401
from src.config import get_settings
from src.raw_materials import models as raw_materials_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Офлайн-режим: SQL генерируется без подключения к БД."""
    context.configure(
        url=settings.sqlalchemy_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Онлайн-режим: подключение с тем же search_path, что у приложения."""
    configuration = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": settings.postgres_schema}},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
