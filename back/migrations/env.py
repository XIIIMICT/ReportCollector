"""
Alembic окружение: асинхронные миграции поверх настроек ReportCollector.

URL и search_path берутся из src.config (POSTGRES_* / DATABASE_URL), а не из
alembic.ini. Схема приложения (rc) задаётся через server_settings, поэтому
миграции создают и изменяют объекты именно в ней.

Важно: URL не передаётся через config.set_main_option("sqlalchemy.url", ...) —
ConfigParser делает %-интерполяцию и падает на спецсимволах пароля,
процент-закодированных в URL (например, «#» -> «%23»). Движок строится
напрямую из настроек приложения.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.auth import models as auth_models  # noqa: F401
from src.collab import models as collab_models  # noqa: F401
from src.config import get_settings
from src.raw_materials import models as raw_materials_models  # noqa: F401
from src.renovation import models as renovation_models  # noqa: F401
from src.renovation import plan as renovation_plan  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

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
    connectable = create_async_engine(
        settings.sqlalchemy_database_url,
        poolclass=pool.NullPool,
        connect_args={
            "server_settings": {
                "search_path": settings.postgres_schema,
                # A blocked migration (e.g. an ALTER waiting behind a lock held
                # by the running app) must fail loudly instead of hanging
                # forever — 30s is far beyond the lock wait these tiny tables
                # ever need.
                "lock_timeout": "30s",
            }
        },
    )
    async with connectable.begin() as connection:
        if not settings.postgres_schema:
            message = "Не задана схема базы данных."
            raise ValueError(message)
        schema = connection.dialect.identifier_preparer.quote_schema(settings.postgres_schema)
        await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
