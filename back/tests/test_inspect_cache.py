"""Valkey-backed cache of renovation plan preflight reports."""

import hashlib

import pytest
from valkey.exceptions import ValkeyError

from src.renovation.import_plan import PlanFileScope
from src.renovation.inspect_cache import (
    INSPECT_CACHE_TTL_SECONDS,
    INSPECT_SCOPE_TTL_SECONDS,
    InspectResultCache,
    inspect_cache_key,
    inspect_scope_key_from_digest,
)
from src.renovation.schemas import RenovationPlanImportResult


class _FakeRedis:
    """Minimal async stand-in: records SET with its TTL and serves GET."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

    async def aclose(self) -> None:
        return None


class _BrokenRedis:
    async def get(self, _key: str) -> str | None:
        msg = "valkey is down"
        raise ValkeyError(msg)

    async def set(self, _key: str, _value: str, **kwargs: object) -> None:
        del kwargs
        msg = "valkey is down"
        raise ValkeyError(msg)


def _report() -> RenovationPlanImportResult:
    return RenovationPlanImportResult(
        status="has_errors",
        duplicate_codes=["CODE (строки 3, 9)"],
        assets=["АО «БСЗ»"],
        new_rows=2,
    )


def test_key_is_the_file_hash() -> None:
    assert inspect_cache_key(b"same") == inspect_cache_key(b"same")
    assert inspect_cache_key(b"same") != inspect_cache_key(b"other")
    assert inspect_cache_key(b"same").startswith("rc:planimport:")


def test_key_carries_the_analyzer_version() -> None:
    """A report written by another analyzer version is never served as this one."""
    from src.renovation.inspect_cache import INSPECT_CACHE_VERSION

    key = inspect_cache_key(b"same")
    assert f"rc:planimport:v{INSPECT_CACHE_VERSION}:" in key
    # The old, version-less key is a different entry: a deploy invalidates it.
    digest = hashlib.sha256(b"same").hexdigest()
    assert key != f"rc:planimport:{digest}"


def test_key_carries_the_asset_and_plan_state_scope() -> None:
    """The same file is cached separately per asset set and plan change time."""
    plain = inspect_cache_key(b"file")
    first = inspect_cache_key(b"file", scope="a1@2026-10-23T10:00:00+00:00")
    same = inspect_cache_key(b"file", scope="a1@2026-10-23T10:00:00+00:00")
    edited = inspect_cache_key(b"file", scope="a1@2026-10-23T11:00:00+00:00")
    other_asset = inspect_cache_key(b"file", scope="a2@2026-10-23T10:00:00+00:00")

    assert first == same
    assert first != plain
    assert first != edited
    assert first != other_asset
    # The scope is the last part of the key: same file, a different scope.
    assert first.startswith(plain + ":")
    assert first == f"{plain}:a1@2026-10-23T10:00:00+00:00"


async def test_scope_separates_entries_for_the_same_file() -> None:
    cache = InspectResultCache(_FakeRedis())
    await cache.set(b"file", _report(), scope="a1@t1")

    assert await cache.get(b"file", scope="a1@t1") is not None
    # Another asset set / a newer plan change misses and is analysed again.
    assert await cache.get(b"file", scope="a1@t2") is None
    assert await cache.get(b"file", scope="a2@t1") is None


async def test_the_file_scope_is_cached_by_digest() -> None:
    """A repeated upload must not re-read the whole workbook to discover assets."""
    redis = _FakeRedis()
    cache = InspectResultCache(redis)
    scope = PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset({"work_type"}))

    assert await cache.get_scope_digest("digest") is None
    await cache.set_scope_digest("digest", scope)

    assert await cache.get_scope_digest("digest") == scope
    assert redis.ttls[inspect_scope_key_from_digest("digest")] == INSPECT_SCOPE_TTL_SECONDS
    # A different file has its own scope entry.
    assert await cache.get_scope_digest("other") is None


async def test_the_scope_cache_round_trips_the_name_mode() -> None:
    cache = InspectResultCache(_FakeRedis())
    scope = PlanFileScope(
        assets=frozenset({'ао "бсз"'}),
        catalogs=frozenset(),
        assets_are_names=True,
    )
    await cache.set_scope_digest("digest", scope)
    restored = await cache.get_scope_digest("digest")
    assert restored == scope
    assert restored is not None
    assert restored.assets_are_names is True


async def test_a_malformed_scope_entry_is_a_miss() -> None:
    redis = _FakeRedis()
    redis.store[inspect_scope_key_from_digest("digest")] = '{"assets": 1}'
    assert await InspectResultCache(redis).get_scope_digest("digest") is None


async def test_report_is_reused_for_the_same_file_with_a_one_hour_ttl() -> None:
    redis = _FakeRedis()
    cache = InspectResultCache(redis)
    payload = b"workbook"
    assert await cache.get(payload) is None

    await cache.set(payload, _report())

    cached = await cache.get(payload)
    assert cached is not None
    assert cached.duplicate_codes == ["CODE (строки 3, 9)"]
    assert redis.ttls[inspect_cache_key(payload)] == INSPECT_CACHE_TTL_SECONDS
    assert INSPECT_CACHE_TTL_SECONDS == 3600


async def test_a_different_file_has_its_own_entry() -> None:
    cache = InspectResultCache(_FakeRedis())
    await cache.set(b"first", _report())

    assert await cache.get(b"first") is not None
    assert await cache.get(b"second") is None


async def test_cache_is_optional_without_valkey() -> None:
    cache = InspectResultCache(None)
    await cache.set(b"payload", _report())
    assert await cache.get(b"payload") is None


async def test_unreachable_valkey_never_fails_the_request() -> None:
    cache = InspectResultCache(_BrokenRedis())
    assert await cache.get(b"payload") is None
    # A write failure is swallowed: the caller still returns the fresh result.
    await cache.set(b"payload", _report())


async def test_malformed_entry_is_treated_as_a_miss() -> None:
    redis = _FakeRedis()
    redis.store[inspect_cache_key(b"payload")] = "{not json"
    cache = InspectResultCache(redis)
    assert await cache.get(b"payload") is None


@pytest.mark.parametrize("payload", [b"", b"\x00\x01"])
async def test_cache_is_skipped_entirely_without_a_client(payload: bytes) -> None:
    assert await InspectResultCache(None).get(payload) is None
