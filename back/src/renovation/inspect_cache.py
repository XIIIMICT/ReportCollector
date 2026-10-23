"""Cache of renovation plan preflight results, keyed by the uploaded file hash."""

import hashlib
from collections.abc import Awaitable
from functools import lru_cache
from typing import Protocol

import redis.asyncio as redis
from redis.backoff import ExponentialBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from src.config import get_settings
from src.renovation.schemas import RenovationPlanImportResult

# One hour: an admin who re-picks the same workbook right after fixing a column
# gets the report instantly; an hour later the file is analysed again.
INSPECT_CACHE_TTL_SECONDS = 3600


def inspect_cache_key(payload: bytes, *, scope: str = "") -> str:
    """
    Valkey key of a workbook: its SHA-256 digest plus the plan state scope.

    The scope names the uploaded assets and the plan's last-change timestamp
    *within those assets*, so an edit in another asset keeps this report valid
    while any touch of a loaded asset recomputes it.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return f"rc:planimport:{digest}:{scope}" if scope else f"rc:planimport:{digest}"


class InspectCacheClient(Protocol):
    """The two commands the cache needs (a real client or a test double)."""

    def get(self, name: str, /) -> Awaitable[str | bytes | None]: ...

    def set(self, name: str, value: str, /, *, ex: int | None = None) -> Awaitable[object]: ...


class InspectResultCache:
    """
    Preflight reports keyed by the file hash.

    A cache is an optimisation, never a dependency: an unreachable Valkey or a
    malformed entry simply means the file is analysed again.
    """

    def __init__(self, client: InspectCacheClient | None) -> None:
        self._redis = client

    async def get(self, payload: bytes, *, scope: str = "") -> RenovationPlanImportResult | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(inspect_cache_key(payload, scope=scope))
        except RedisError:
            return None
        if raw is None:
            return None
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            return RenovationPlanImportResult.model_validate_json(text)
        except ValueError:
            return None

    async def set(self, payload: bytes, result: RenovationPlanImportResult, *, scope: str = "") -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                inspect_cache_key(payload, scope=scope),
                result.model_dump_json(),
                ex=INSPECT_CACHE_TTL_SECONDS,
            )
        except RedisError:
            # A cache write failure must never fail the request.
            return

    async def close(self) -> None:
        closer = getattr(self._redis, "aclose", None)
        if closer is not None:
            await closer()


@lru_cache(maxsize=1)
def get_inspect_cache() -> InspectResultCache:
    """Valkey-backed cache; disabled (None client) when VALKEY_URL is empty."""
    url = get_settings().valkey_url.strip()
    if not url:
        return InspectResultCache(None)
    client = redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
        health_check_interval=30,
        retry=Retry(ExponentialBackoff(base=0.2, cap=1.5), retries=3),
        retry_on_timeout=True,
    )
    return InspectResultCache(client)


def clear_inspect_cache() -> None:
    """Drop the singleton (tests and configuration reloads)."""
    get_inspect_cache.cache_clear()
