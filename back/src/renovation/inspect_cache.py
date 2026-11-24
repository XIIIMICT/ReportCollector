"""Cache of renovation plan preflight results, keyed by the uploaded file hash."""

import hashlib
import json
from collections.abc import Awaitable
from functools import lru_cache
from typing import Protocol

import valkey.asyncio as valkey
from valkey.backoff import ExponentialBackoff
from valkey.exceptions import ValkeyError
from valkey.retry import Retry

from src.config import get_settings
from src.metrics import CACHE_HITS_TOTAL, CACHE_MISSES_TOTAL
from src.observability import observe_valkey
from src.renovation.import_plan import PlanFileScope
from src.renovation.renovation_id import RENOVATION_ID_FORMULA_ENABLED
from src.renovation.schemas import RenovationPlanImportResult

# One hour: an admin who re-picks the same workbook right after fixing a column
# gets the report instantly; an hour later the file is analysed again.
INSPECT_CACHE_TTL_SECONDS = 3600

# The file-derived scope (assets and catalogs the workbook mentions) is a
# property of the BYTES, not of the plan: it never goes stale while the file is
# unchanged, so it is kept much longer than the report. This entry is what lets
# a repeated upload skip the full scope-discovery read of a large workbook.
INSPECT_SCOPE_TTL_SECONDS = 7 * 24 * 3600

# Version of the analyzer whose report the entry holds. Part of every key: a
# cached report outlives a deploy by up to the TTL, so without this an entry
# written by a buggy preflight (the numbering row read as data, twice) kept
# being served after the fix — the operator re-uploaded the same file and saw
# the old analysis. Bump it whenever the report's content changes. The renovation
# ID generator is a behavior-affecting config flag, so a flip invalidates all
# entries without a code deploy.
INSPECT_CACHE_VERSION = f"7:{int(RENOVATION_ID_FORMULA_ENABLED)}"


def inspect_cache_key(payload: bytes, *, scope: str = "", suffix: str | None = None) -> str:
    """
    Valkey key of a workbook: its SHA-256 digest plus the plan state scope.

    The scope names the uploaded assets and the plan's last-change timestamp
    *within those assets*, so an edit in another asset keeps this report valid
    while any touch of a loaded asset recomputes it. The container suffix is in
    the key too: identical bytes named `.xlsb` and `.xlsx` go through different
    readers and must never share a report.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return inspect_cache_key_from_digest(digest, scope=scope, suffix=suffix)


def inspect_cache_key_from_digest(digest: str, *, scope: str = "", suffix: str | None = None) -> str:
    """Build a cache key when the upload was hashed without retaining its bytes."""
    base = f"rc:planimport:v{INSPECT_CACHE_VERSION}:{digest}"
    if suffix:
        base = f"{base}:{suffix}"
    return f"{base}:{scope}" if scope else base


def inspect_scope_key_from_digest(digest: str, *, suffix: str | None = None) -> str:
    """Key of the file-derived scope (assets/catalogs) for one workbook digest."""
    base = f"rc:planimport:v{INSPECT_CACHE_VERSION}:scope:{digest}"
    return f"{base}:{suffix}" if suffix else base


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

    async def get(
        self, payload: bytes, *, scope: str = "", suffix: str | None = None
    ) -> RenovationPlanImportResult | None:
        return await self.get_digest(hashlib.sha256(payload).hexdigest(), scope=scope, suffix=suffix)

    async def get_digest(
        self, digest: str, *, scope: str = "", suffix: str | None = None
    ) -> RenovationPlanImportResult | None:
        if self._redis is None:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect").inc()
            return None
        try:
            with observe_valkey("inspect_cache_get"):
                raw = await self._redis.get(inspect_cache_key_from_digest(digest, scope=scope, suffix=suffix))
        except ValkeyError:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect").inc()
            return None
        if raw is None:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect").inc()
            return None
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            result = RenovationPlanImportResult.model_validate_json(text)
        except ValueError:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect").inc()
            return None
        CACHE_HITS_TOTAL.labels(cache="plan_import_inspect").inc()
        return result

    async def get_scope_digest(self, digest: str, *, suffix: str | None = None) -> PlanFileScope | None:
        """
        Return the file-derived scope cached for this digest, or None.

        Without this entry every upload — including a cache HIT on the report —
        had to read the whole workbook once more to discover its assets and
        catalogs, so a large file looked just as slow as the first run.
        """
        if self._redis is None:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        try:
            with observe_valkey("inspect_scope_get"):
                raw = await self._redis.get(inspect_scope_key_from_digest(digest, suffix=suffix))
        except ValkeyError:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        if raw is None:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text)
        except ValueError:
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        if not isinstance(payload, dict):
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        assets = payload.get("assets")
        catalogs = payload.get("catalogs")
        assets_are_names = payload.get("assets_are_names")
        if not isinstance(assets, list) or not isinstance(catalogs, list) or not isinstance(assets_are_names, bool):
            CACHE_MISSES_TOTAL.labels(cache="plan_import_inspect_scope").inc()
            return None
        CACHE_HITS_TOTAL.labels(cache="plan_import_inspect_scope").inc()
        return PlanFileScope(
            assets=frozenset(str(value) for value in assets if isinstance(value, str)),
            catalogs=frozenset(str(value) for value in catalogs if isinstance(value, str)),
            assets_are_names=assets_are_names,
        )

    async def set_scope_digest(self, digest: str, scope: PlanFileScope, *, suffix: str | None = None) -> None:
        """Remember the scope of these bytes; a failure is only a slower retry."""
        if self._redis is None:
            return
        payload = json.dumps(
            {
                "assets": sorted(scope.assets),
                "catalogs": sorted(scope.catalogs),
                "assets_are_names": scope.assets_are_names,
            },
            ensure_ascii=False,
        )
        try:
            with observe_valkey("inspect_scope_set"):
                await self._redis.set(
                    inspect_scope_key_from_digest(digest, suffix=suffix),
                    payload,
                    ex=INSPECT_SCOPE_TTL_SECONDS,
                )
        except ValkeyError:
            return

    async def set(
        self, payload: bytes, result: RenovationPlanImportResult, *, scope: str = "", suffix: str | None = None
    ) -> None:
        await self.set_digest(hashlib.sha256(payload).hexdigest(), result, scope=scope, suffix=suffix)

    async def set_digest(
        self,
        digest: str,
        result: RenovationPlanImportResult,
        *,
        scope: str = "",
        suffix: str | None = None,
    ) -> None:
        if self._redis is None:
            return
        try:
            with observe_valkey("inspect_cache_set"):
                await self._redis.set(
                    inspect_cache_key_from_digest(digest, scope=scope, suffix=suffix),
                    result.model_dump_json(),
                    ex=INSPECT_CACHE_TTL_SECONDS,
                )
        except ValkeyError:
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
    client = valkey.from_url(
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
