"""Streaming HTTP instrumentation and periodic runtime sampling."""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from prometheus_client import ProcessCollector
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from .app_logging import log_event
from .metrics import (
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_RESPONSE_FAILURES,
    OPERATION_DURATION,
    REGISTRY,
    RUNTIME_DISK_FREE_BYTES,
    RUNTIME_DISK_SIZE_BYTES,
    RUNTIME_EVENT_LOOP_LAG,
    VALKEY_OPERATION_DURATION,
    observe_http_request,
    observe_http_sizes,
)

LOGGER = logging.getLogger("reportcollector.application")
REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
# Linux /proc gives current RSS (not peak RSS), CPU seconds, FDs and start time.
# On platforms without /proc the collector intentionally emits no samples.
PROCESS_COLLECTOR = ProcessCollector(registry=REGISTRY)


@contextmanager
def observe_valkey(operation: str) -> Generator[None]:
    """Measure a constant operation name, never keys, rooms, scripts or payloads."""
    started = perf_counter()
    outcome = "ok"
    try:
        yield
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except ValkeyTimeoutError:
        outcome = "timeout"
        raise
    except ValkeyConnectionError:
        outcome = "connection_error"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        VALKEY_OPERATION_DURATION.labels(operation=operation, outcome=outcome).observe(perf_counter() - started)


def measured_valkey[**P, T](function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Use the declared method name as the bounded metric label."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with observe_valkey(getattr(function, "__name__", "valkey_operation")):
            return await function(*args, **kwargs)

    return wrapped


@contextmanager
def observe_phase(operation: str, phase: str) -> Generator[None]:
    """Use only constant operation/phase names; IDs belong in logs."""
    started = perf_counter()
    outcome = "ok"
    try:
        yield
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "error"
        raise
    finally:
        duration = perf_counter() - started
        OPERATION_DURATION.labels(operation=operation, phase=phase, outcome=outcome).observe(duration)
        # Per-batch phases stay in metrics to keep log volume bounded.
        if phase in {"inspect", "apply", "generate"}:
            log_event(
                LOGGER,
                "data_operation",
                "Этап обработки данных завершён.",
                operation=operation,
                phase=phase,
                outcome=outcome,
                request_id=REQUEST_ID.get(),
                duration_ms=round(duration * 1000, 1),
            )


class RequestMetricsMiddleware:
    """Count actual ASGI bytes without reading ahead or buffering a body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        request_id = str(uuid4())
        token = REQUEST_ID.set(request_id)
        request_bytes = response_bytes = 0
        status = 500
        complete = False
        pathsend_size = 0
        HTTP_REQUESTS_IN_PROGRESS.inc()

        async def measured_receive() -> Message:
            nonlocal request_bytes
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
            return message

        async def measured_send(message: Message) -> None:
            nonlocal status, response_bytes, complete, pathsend_size
            if message["type"] == "http.response.start":
                status = message["status"]
                pathsend_size = next(
                    (int(v) for k, v in message.get("headers", []) if k.lower() == b"content-length"), 0
                )
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", request_id.encode())]}
            await send(message)
            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                complete = not message.get("more_body", False)
            elif message["type"] == "http.response.pathsend":
                response_bytes += pathsend_size
                complete = True

        try:
            await self.app(scope, measured_receive, measured_send)
        except Exception:
            LOGGER.exception(
                "Request failed", extra={"request_id": request_id, "path": scope["path"], "method": scope["method"]}
            )
            raise
        finally:
            HTTP_REQUESTS_IN_PROGRESS.dec()
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            duration = perf_counter() - started
            observe_http_request(method=scope["method"], route=route, status=status, duration_seconds=duration)
            observe_http_sizes(
                method=scope["method"],
                route=route,
                status=status,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )
            if not complete:
                HTTP_RESPONSE_FAILURES.labels(method=scope["method"], route=route).inc()
            if scope["path"] not in {"/health", "/ready", "/metrics"}:
                level = (
                    logging.ERROR
                    if status >= 500 or not complete
                    else logging.WARNING
                    if status >= 400
                    else logging.INFO
                )
                LOGGER.log(
                    level,
                    "Запрос обработан.",
                    extra={
                        "event": "http_request_error"
                        if status >= 500 or not complete
                        else "http_request_failed"
                        if status >= 400
                        else "http_request",
                        "method": scope["method"],
                        "path": scope["path"],
                        "route": route,
                        "status_code": status,
                        "duration_ms": round(duration * 1000, 1),
                        "request_id": request_id,
                        "request_bytes": request_bytes,
                        "response_bytes": response_bytes,
                        "outcome": "complete" if complete else "interrupted",
                    },
                )
            REQUEST_ID.reset(token)


async def sample_runtime_metrics(*, log_dir: Path, export_dir: Path) -> None:
    """Sample loop delay each second; filesystem calls run off-loop every 15s."""
    tick = 0
    while True:
        started = perf_counter()
        await asyncio.sleep(1)
        RUNTIME_EVENT_LOOP_LAG.observe(max(0, perf_counter() - started - 1))
        if tick % 15 == 0:
            for storage, path in (("logs", log_dir), ("exports", export_dir)):
                try:
                    usage = await asyncio.to_thread(shutil.disk_usage, path)
                except OSError:
                    RUNTIME_DISK_FREE_BYTES.labels(storage=storage).set(float("nan"))
                    RUNTIME_DISK_SIZE_BYTES.labels(storage=storage).set(float("nan"))
                else:
                    RUNTIME_DISK_FREE_BYTES.labels(storage=storage).set(usage.free)
                    RUNTIME_DISK_SIZE_BYTES.labels(storage=storage).set(usage.total)
        tick += 1
