from time import perf_counter
from typing import TYPE_CHECKING, cast

from sqlalchemy import event
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlmodel.ext.asyncio.session import AsyncSession

from .config import get_settings
from .metrics import (
    DB_CONNECTION_CHECKOUTS_TOTAL,
    DB_CONNECTION_HOLD_DURATION,
    DB_CONNECTION_INVALIDATIONS_TOTAL,
    DB_CONNECTIONS_IN_USE,
    DB_POOL_ACQUIRE_DURATION,
    DB_POOL_CAPACITY,
    DB_POOL_OVERFLOW,
    DB_POOL_SIZE,
    DB_QUERIES_TOTAL,
    DB_QUERY_DURATION,
    DB_QUERY_ERRORS_TOTAL,
    DB_QUERY_FAILURES,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.pool import ConnectionPoolEntry

settings = get_settings()


class MeasuredPool(AsyncAdaptedQueuePool):
    """
    Measure pool acquisition including wait/setup, excluding pre-ping.

    SQLAlchemy's checkout event fires after waiting. The queue hook is needed
    to include timeouts and lazy acquisition by HTTP and background sessions.
    It delegates all queue and connection behaviour to the library.
    """

    def _do_get(self) -> ConnectionPoolEntry:
        started = perf_counter()
        outcome = "ok"
        try:
            return super()._do_get()
        except PoolTimeoutError:
            outcome = "timeout"
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            DB_POOL_ACQUIRE_DURATION.labels(outcome=outcome).observe(perf_counter() - started)


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
    poolclass=MeasuredPool,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    pool_recycle=settings.database_pool_recycle_seconds,
)
async_session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


# Read state at scrape time; checkin callbacks run before the pool returns its
# slot, so updating gauges in that callback yields stale occupancy/overflow.
DB_POOL_SIZE.set_function(lambda: cast("MeasuredPool", async_engine.pool).size())
DB_POOL_OVERFLOW.set_function(lambda: max(0, cast("MeasuredPool", async_engine.pool).overflow()))
DB_CONNECTIONS_IN_USE.set_function(lambda: cast("MeasuredPool", async_engine.pool).checkedout())
DB_POOL_CAPACITY.set(settings.database_pool_size + settings.database_max_overflow)


@event.listens_for(async_engine.sync_engine, "checkout")
def _on_checkout(_dbapi_connection: object, connection_record: object, _connection_proxy: object) -> None:
    DB_CONNECTION_CHECKOUTS_TOTAL.inc()
    info = getattr(connection_record, "record_info", None)
    if isinstance(info, dict):
        info["reportcollector_checkout_started"] = perf_counter()


@event.listens_for(async_engine.sync_engine, "checkin")
def _on_checkin(_dbapi_connection: object, connection_record: object) -> None:
    info = getattr(connection_record, "record_info", None)
    if isinstance(info, dict):
        started = info.pop("reportcollector_checkout_started", None)
        if isinstance(started, float):
            # This measures time from checkout callback to checkin. It is the
            # useful occupied-connection interval and stays driver-agnostic.
            DB_CONNECTION_HOLD_DURATION.observe(perf_counter() - started)


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
    started = cast("dict[str, object]", context.__dict__).pop("_reportcollector_query_started", None)
    if isinstance(started, float):
        DB_QUERY_DURATION.observe(perf_counter() - started)
        DB_QUERIES_TOTAL.inc()


@event.listens_for(async_engine.sync_engine, "handle_error")
def _on_query_error(exception_context: object) -> None:
    context = getattr(exception_context, "execution_context", None)
    started = context.__dict__.pop("_reportcollector_query_started", None) if context is not None else None
    if isinstance(started, float):
        DB_QUERY_DURATION.observe(perf_counter() - started)
        DB_QUERIES_TOTAL.inc()
        DB_QUERY_ERRORS_TOTAL.inc()
        original = getattr(exception_context, "original_exception", None)
        reason = {"57014": "statement_timeout_or_cancel", "55P03": "lock_timeout", "40P01": "deadlock"}.get(
            getattr(original, "sqlstate", None), "other"
        )
        DB_QUERY_FAILURES.labels(reason=reason).inc()


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
