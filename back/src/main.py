import asyncio
import itertools
import logging
import time
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
)
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from .api import api_router
from .app_logging import configure_application_logging, log_error, log_event, log_warning, monotonic_ms
from .collab.outbox import run_outbox
from .collab.store import get_collab_store
from .common.export_jobs import table_export_jobs
from .config import get_settings
from .database import async_session_factory
from .metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
    REGISTRY,
)
from .renovation.export_jobs import export_jobs

settings = get_settings()
configure_application_logging(settings.log_file)
LOGGER = logging.getLogger("reportcollector.application")

# Simple in-memory rate limiter for auth endpoints
# 20 requests per minute per client for /api/auth/.
_rate_limit_store: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMIT_MAX_AUTH = 20  # max auth requests per window
# Hard cap on tracked clients: a flood of distinct keys must not grow the dict
# without bound. Empty buckets are swept, and if the cap is still exceeded the
# oldest buckets are dropped.
_RATE_LIMIT_MAX_KEYS = 10_000
_RATE_LIMIT_SWEEP_EVERY = 256
_rate_limit_ticks = itertools.count()


def _client_key(request: Request) -> str:
    """
    Identify the client for rate limiting.

    By default (TRUSTED_PROXY_HOPS=0) the socket peer is used and
    X-Forwarded-For is ignored, because a client can spoof it. Behind N trusted
    proxies, the client is the Nth-from-right XFF entry (each proxy appends the
    address it saw).
    """
    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(parts) >= hops:
                return parts[-hops]
    return request.client.host if request.client else "unknown"


def _sweep_rate_limit(now: float) -> None:
    """Drop empty/expired buckets so the store cannot grow without bound."""
    cutoff = now - _RATE_LIMIT_WINDOW
    for key in [key for key, stamps in _rate_limit_store.items() if not any(t > cutoff for t in stamps)]:
        del _rate_limit_store[key]
    # Still too many distinct clients — drop the oldest buckets (by newest stamp).
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
        for key, _ in sorted(_rate_limit_store.items(), key=lambda item: max(item[1], default=0.0))[
            : len(_rate_limit_store) - _RATE_LIMIT_MAX_KEYS
        ]:
            del _rate_limit_store[key]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    log_event(LOGGER, "application_started", "Приложение ReportCollector запущено.")

    dispatcher = asyncio.create_task(run_outbox())
    await export_jobs.start()
    await table_export_jobs.start()
    try:
        yield
    finally:
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher
        await export_jobs.stop()
        await table_export_jobs.stop()
        await get_collab_store().close()
    log_event(LOGGER, "application_stopped", "Приложение ReportCollector остановлено.")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins or [],
    allow_credentials=False,
    allow_methods=["DELETE", "GET", "PATCH", "POST"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.middleware("http")
async def rate_limit_auth(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path.startswith("/api/auth/"):
        client_ip = _client_key(request)
        now = time.monotonic()
        # No locking: asyncio is single-threaded, no concurrent access to _rate_limit_store.
        cutoff = now - _RATE_LIMIT_WINDOW
        timestamps = [t for t in _rate_limit_store.get(client_ip, []) if t > cutoff]
        if len(timestamps) >= _RATE_LIMIT_MAX_AUTH:
            return Response(
                content='{"detail":"Слишком много запросов. Попробуйте позже."}',
                status_code=429,
                headers={"Content-Type": "application/json", "Retry-After": "60"},
            )
        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        if next(_rate_limit_ticks) % _RATE_LIMIT_SWEEP_EVERY == 0:
            _sweep_rate_limit(now)
    return await call_next(request)


@app.middleware("http")
async def log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    start = monotonic_ms()
    request_id = str(uuid4())
    HTTP_REQUESTS_IN_PROGRESS.inc()
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception(
            "Request failed", extra={"request_id": request_id, "path": request.url.path, "method": request.method}
        )
        raise
    finally:
        HTTP_REQUESTS_IN_PROGRESS.dec()
    response.headers["X-Request-ID"] = request_id
    duration_ms = round(monotonic_ms() - start, 1)
    status = response.status_code
    path = request.url.path

    # The ROUTE TEMPLATE, not the raw path: a per-row uuid would create one
    # Prometheus time series per row. `scope["route"]` is set by the router, so
    # an unmatched path (a 404) falls back to a single shared bucket.
    route = getattr(request.scope.get("route"), "path", None) or "unmatched"
    HTTP_REQUESTS_TOTAL.labels(method=request.method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=request.method, route=route).observe(duration_ms / 1000)

    # Skip frequent healthcheck requests — they spam the log. The METRICS above
    # are recorded for them too: a scrape should still see the readiness probe.
    if path in {"/health", "/ready"}:
        return response

    if status >= 500:
        log_error(
            LOGGER,
            "http_request_error",
            "Серверная ошибка при обработке запроса.",
            method=request.method,
            path=path,
            status_code=status,
            duration_ms=duration_ms,
            request_id=request_id,
        )
    elif status >= 400:
        log_warning(
            LOGGER,
            "http_request_failed",
            "Запрос завершился клиентской ошибкой.",
            method=request.method,
            path=path,
            status_code=status,
            duration_ms=duration_ms,
            request_id=request_id,
        )
    elif status < 400:
        log_event(
            LOGGER,
            "http_request",
            "Запрос обработан.",
            method=request.method,
            path=path,
            status_code=status,
            duration_ms=duration_ms,
            request_id=request_id,
        )
    return response


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """
    Prometheus scrape endpoint.

    Unauthenticated by design: Prometheus scrapes it over the internal network.
    The payload is aggregate counts and histogram buckets only — no user data,
    and route TEMPLATES rather than paths, so no ids leak.
    """
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/ready")
async def readiness() -> JSONResponse:
    """Readiness checks dependencies without exposing connection details."""
    try:
        async with asyncio.timeout(3):
            async with async_session_factory() as session:
                # Also reject an instance started before its required migration.
                await (await session.connection()).execute(text("SELECT id FROM collab_outbox LIMIT 0"))
            await get_collab_store().room_members("readiness")
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
