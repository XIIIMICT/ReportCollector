from time import perf_counter
from typing import TYPE_CHECKING, cast

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from .config import get_settings
from .metrics import (
    DB_CONNECTION_CHECKOUTS_TOTAL,
    DB_CONNECTION_INVALIDATIONS_TOTAL,
    DB_CONNECTIONS_IN_USE,
    DB_QUERIES_TOTAL,
    DB_QUERY_DURATION,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

settings = get_settings()

async_engine = create_async_engine(
    settings.sqlalchemy_database_url,
    connect_args={
        "server_settings": {
            "search_path": settings.postgres_schema,
            "statement_timeout": str(settings.database_statement_timeout_ms),
            "lock_timeout": str(settings.database_lock_timeout_ms),
        }
    },
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    pool_recycle=settings.database_pool_recycle_seconds,
)
async_session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(async_engine.sync_engine, "checkout")
def _on_checkout(_dbapi_connection: object, _connection_record: object, _connection_proxy: object) -> None:
    DB_CONNECTIONS_IN_USE.inc()
    DB_CONNECTION_CHECKOUTS_TOTAL.inc()


@event.listens_for(async_engine.sync_engine, "checkin")
def _on_checkin(_dbapi_connection: object, _connection_record: object) -> None:
    DB_CONNECTIONS_IN_USE.dec()


@event.listens_for(async_engine.sync_engine, "invalidate")
def _on_invalidate(_dbapi_connection: object, _connection_record: object, _exception: BaseException | None) -> None:
    DB_CONNECTION_INVALIDATIONS_TOTAL.inc()


@event.listens_for(async_engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(
    _connection: object,
    _cursor: object,
    _statement: str,
    _parameters: object,
    context: object,
    _executemany: bool,
) -> None:
    cast("dict[str, object]", context.__dict__)["_reportcollector_query_started"] = perf_counter()


@event.listens_for(async_engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(
    _connection: object,
    _cursor: object,
    _statement: str,
    _parameters: object,
    context: object,
    _executemany: bool,
) -> None:
    started = cast("dict[str, object]", context.__dict__).get("_reportcollector_query_started")
    if isinstance(started, float):
        DB_QUERY_DURATION.observe(perf_counter() - started)
        DB_QUERIES_TOTAL.inc()


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
