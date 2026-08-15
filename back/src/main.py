import logging
import time
from collections import defaultdict
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
)
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .app_logging import configure_application_logging, log_error, log_event, log_warning, monotonic_ms
from .collab.store import get_collab_store
from .config import get_settings

settings = get_settings()
configure_application_logging(settings.log_file)
LOGGER = logging.getLogger("reportcollector.application")

# Simple in-memory rate limiter for auth endpoints
# 20 requests per minute per IP for /api/auth/
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMIT_MAX_AUTH = 20  # max auth requests per window


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log_event(LOGGER, "application_started", "Приложение ReportCollector запущено.")

    yield

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
        # Use only request.client.host — not X-Forwarded-For — to avoid IP spoofing.
        # Behind a reverse proxy (nginx) client.host is always the proxy IP — acceptable
        # for simple per-worker-process rate limiting.
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        # No locking: asyncio is single-threaded, no concurrent access to _rate_limit_store.
        timestamps = _rate_limit_store[client_ip]
        cutoff = now - _RATE_LIMIT_WINDOW
        _rate_limit_store[client_ip] = [t for t in timestamps if t > cutoff]
        if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_MAX_AUTH:
            return Response(
                content='{"detail":"Слишком много запросов. Попробуйте позже."}',
                status_code=429,
                headers={"Content-Type": "application/json", "Retry-After": "60"},
            )
        _rate_limit_store[client_ip].append(now)
    return await call_next(request)


@app.middleware("http")
async def log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    start = monotonic_ms()
    response = await call_next(request)
    duration_ms = round(monotonic_ms() - start, 1)
    status = response.status_code
    path = request.url.path

    # Skip frequent healthcheck requests — they spam the log
    if path == "/health":
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
        )
    return response


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
