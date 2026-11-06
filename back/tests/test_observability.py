"""Regression coverage for metrics on real failure and streaming boundaries."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.types import Message, Receive, Scope, Send
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from src import database
from src.metrics import REGISTRY
from src.observability import REQUEST_ID, RequestMetricsMiddleware


def sample(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0


@pytest.mark.parametrize("fail", [False, True])
async def test_stream_counts_bytes_until_completion_or_failure(fail: bool) -> None:
    route = "/stream/{id}"
    labels = {"method": "POST", "route": route}
    received = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ]
    )
    sent = []
    baseline = sample("http_requests_in_progress")
    before_bytes = sample("http_response_bytes_sum", {**labels, "status": "200"})
    before_in = sample("http_request_upload_bytes_sum", labels)
    before_fail = sample("http_response_failures_total", labels)

    async def receive() -> Message:
        return next(received)

    async def send(message: Message) -> None:
        sent.append(message)

    async def application(scope: Scope, receive_fn: Receive, send_fn: Send) -> None:
        scope["route"] = SimpleNamespace(path=route)
        assert REQUEST_ID.get()
        assert (await receive_fn())["body"] == b"abc"
        assert (await receive_fn())["body"] == b"de"
        await send_fn({"type": "http.response.start", "status": 200, "headers": []})
        await send_fn({"type": "http.response.body", "body": b"first", "more_body": True})
        assert sample("http_requests_in_progress") == baseline + 1
        if fail:
            message = "stream interrupted"
            raise RuntimeError(message)
        await send_fn({"type": "http.response.body", "body": b"last", "more_body": False})

    middleware = RequestMetricsMiddleware(application)
    scope: Scope = {"type": "http", "method": "POST", "path": "/stream/private-id"}
    if fail:
        with pytest.raises(RuntimeError, match="stream interrupted"):
            await middleware(scope, receive, send)
    else:
        await middleware(scope, receive, send)
    assert sample("http_requests_in_progress") == baseline
    assert sample("http_response_bytes_sum", {**labels, "status": "200"}) - before_bytes == (5 if fail else 9)
    assert sample("http_request_upload_bytes_sum", labels) - before_in == 5
    assert sample("http_response_failures_total", labels) - before_fail == int(fail)
    if fail:
        assert sample("http_last_failure_timestamp_seconds") > 0
    assert dict(sent[0]["headers"])[b"x-request-id"]
    assert REQUEST_ID.get() is None


async def test_pool_timeout_and_failed_statements_are_observed(test_engine):
    engine = create_async_engine(
        test_engine.url, poolclass=database.MeasuredPool, pool_size=1, max_overflow=0, pool_timeout=0.03
    )
    for name, callback in (
        ("before_cursor_execute", database._before_cursor_execute),
        ("after_cursor_execute", database._after_cursor_execute),
        ("handle_error", database._on_query_error),
        ("checkout", database._on_checkout),
        ("checkin", database._on_checkin),
    ):
        event.listen(engine.sync_engine, name, callback)
    before_timeout = sample("db_pool_acquire_seconds_count", {"outcome": "timeout"})
    before_queries = sample("db_queries_total")
    before_errors = sample("db_query_errors_total")
    before_duration = sample("db_query_duration_seconds_count")
    before_hold = sample("db_connection_hold_seconds_count")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            with pytest.raises(PoolTimeoutError):
                async with engine.connect():
                    pytest.fail("exhausted pool admitted another connection")
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT 1 / 0"))
            await conn.rollback()
        assert sample("db_pool_acquire_seconds_count", {"outcome": "timeout"}) == before_timeout + 1
        assert sample("db_queries_total") == before_queries + 2
        assert sample("db_query_errors_total") == before_errors + 1
        assert sample("db_query_duration_seconds_count") == before_duration + 2
        assert sample("db_connection_hold_seconds_count") == before_hold + 1
        assert getattr(engine.pool, "checkedout", lambda: -1)() == 0
    finally:
        await engine.dispose()


async def test_parser_closes_without_producing_more_after_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.renovation import import_apply

    seen = []
    closed = Event()

    def parse(_source: object, _size: int, _suffix: str | None = None) -> Iterator[list[object]]:
        try:
            for number in range(10):
                seen.append(number)
                yield []
        finally:
            closed.set()

    monkeypatch.setattr(import_apply, "iter_plan_row_batches", parse)

    async def consume() -> None:
        async with aclosing(import_apply._stream_parsed_batches(BytesIO())) as batches:
            async for _batch in batches:
                message = "write failed"
                raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="write failed"):
        await consume()
    assert closed.is_set()
    assert seen == [0]


async def test_cancelled_parser_waits_for_current_read_before_closing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.renovation import import_apply

    entered, release, closed = Event(), Event(), Event()

    def parse(_source: object, _size: int, _suffix: str | None = None) -> Iterator[list[object]]:
        try:
            entered.set()
            if not release.wait(5):
                message = "test did not release parser"
                raise RuntimeError(message)
            yield []
        finally:
            closed.set()

    monkeypatch.setattr(import_apply, "iter_plan_row_batches", parse)
    batches = import_apply._stream_parsed_batches(BytesIO())
    read = asyncio.create_task(anext(batches))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        read.cancel()
        await asyncio.sleep(0)
        assert not closed.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await read
    assert closed.is_set()


async def test_failed_cache_read_stays_optional_but_is_observable() -> None:
    from src.renovation.inspect_cache import InspectResultCache

    class UnavailableCache:
        async def get(self, _name: str, /) -> None:
            message = "cache unavailable"
            raise ValkeyTimeoutError(message)

        async def set(self, _name: str, _value: str, /, *, ex: int | None = None) -> None:  # noqa: ARG002
            pytest.fail("a failed read must not write to the cache")

    labels = {"operation": "inspect_cache_get", "outcome": "timeout"}
    before = sample("valkey_operation_duration_seconds_count", labels)
    cache = InspectResultCache(UnavailableCache())
    assert await cache.get_digest("private-digest", scope="private-scope") is None
    assert sample("valkey_operation_duration_seconds_count", labels) == before + 1


@pytest.mark.parametrize("fail_on_subscribe", [False, True])
async def test_pubsub_transport_failure_is_counted_and_closed(fail_on_subscribe: bool) -> None:
    from src.collab.store import ValkeyStore

    closed = False

    async def subscribe(_channel: str) -> None:
        if fail_on_subscribe:
            message = "subscribe failed"
            raise ValkeyConnectionError(message)

    async def listen() -> AsyncIterator[dict[str, str]]:
        yield {"type": "subscribe"}
        message = "listen failed"
        raise ValkeyConnectionError(message)

    async def close() -> None:
        nonlocal closed
        closed = True

    pubsub = SimpleNamespace(subscribe=subscribe, listen=listen, aclose=close)
    store = ValkeyStore("redis://127.0.0.1:1/15")
    await store.close()
    store._pubsub_redis = SimpleNamespace(pubsub=lambda: pubsub)
    before = sample("valkey_subscription_failures_total")
    with pytest.raises(ValkeyConnectionError):
        await anext(store.subscribe("private-room"))
    assert closed
    assert sample("valkey_subscription_failures_total") == before + 1
