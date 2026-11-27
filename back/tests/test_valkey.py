"""Exercise the production store across independent connections using unique keys."""

import asyncio
import os
from uuid import uuid4

import pytest

from src.collab.store import ValkeyStore


@pytest.fixture
async def valkey_pair():
    url = os.environ.get("TEST_VALKEY_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("TEST_VALKEY_URL is required in CI")
        pytest.skip("Set TEST_VALKEY_URL to run real Valkey integration tests")
    first, second = ValkeyStore(url), ValkeyStore(url)
    try:
        yield first, second
    finally:
        await first.close()
        await second.close()


async def test_lease_ownership_and_expiry(valkey_pair) -> None:
    first, second = valkey_pair
    key = f"rc:test:{uuid4()}"
    owner, other = {"id": "first"}, {"id": "second"}
    assert await first.acquire_lease(key, owner, ttl=1) is None
    assert await second.acquire_lease(key, other, ttl=1) == owner
    assert not await second.renew_lease(key, other)
    assert not await second.release_lease(key, other)
    assert await first.renew_lease(key, owner, ttl=1)
    async with asyncio.timeout(5):
        while await second.get_lease(key) is not None:  # noqa: ASYNC110 - poll server-side TTL, no local event exists
            await asyncio.sleep(0.05)
    assert await second.acquire_lease(key, other, ttl=2) is None
    assert await second.release_lease(key, other)


async def test_batch_acquire_grants_a_lease_expired_before_the_script(valkey_pair) -> None:
    first, second = valkey_pair
    expired = f"rc:test:batch-expired:{uuid4()}"
    held = f"rc:test:batch-held:{uuid4()}"
    owner, other = {"id": "first"}, {"id": "second"}
    try:
        assert await first.acquire_lease(expired, owner, ttl=10) is None
        assert await first.acquire_lease(held, owner, ttl=10) is None
        # Force server-side expiry deterministically — no local sleep is evidence.
        assert await first._redis.pexpireat(expired, 1)
        assert await second.get_lease(expired) is None
        assert await second.acquire_leases([expired, held], other) == [None, owner]
        assert await first.get_lease(expired) == other
        assert await first.get_lease(held) == owner
    finally:
        await first.release_leases([expired, held], owner)
        await second.release_leases([expired, held], other)


async def test_batch_leases_keep_per_cell_ttl_and_denials_do_not_renew(valkey_pair) -> None:
    first, second = valkey_pair
    keys = [f"rc:test:batch-ttl:{uuid4()}:{index}" for index in range(3)]
    owner, other = {"id": "first"}, {"id": "second"}
    try:
        assert await first.acquire_leases(keys, owner, ttl=8) == [None, None, None]
        assert await second.acquire_leases(keys, other, ttl=1) == [owner, owner, owner]
        for key in keys:
            remaining_ms = await first._redis.pttl(key)
            assert 3000 < remaining_ms <= 8000
        assert await first.release_leases(keys, owner) == [True, True, True]
        assert await second.release_leases(keys, other) == [False, False, False]
    finally:
        await first.release_leases(keys, owner)
        await second.release_leases(keys, other)


async def test_batch_scripts_reload_deterministically_after_noscript(valkey_pair, monkeypatch) -> None:
    first, second = valkey_pair
    keys = [f"rc:test:batch-noscript:{uuid4()}:{index}" for index in range(2)]
    owner = {"id": "first"}
    loads: list[str] = []
    original_load = second._redis.script_load

    async def counted_load(script: str) -> str:
        loads.append(script)
        return await original_load(script)

    monkeypatch.setattr(second._redis, "script_load", counted_load)
    try:
        await first._redis.script_flush()
        assert await second.acquire_leases(keys, owner) == [None, None]
        assert len(loads) == 1
        assert all(await second._redis.script_exists(second._batch_acquire.sha))
        await first._redis.script_flush()
        assert await second.release_leases(keys, owner) == [True, True]
        assert len(loads) == 2
        assert all(await second._redis.script_exists(second._batch_release.sha))
    finally:
        await first.release_leases(keys, owner)


async def test_batch_race_between_independent_clients_has_one_owner_per_cell(valkey_pair) -> None:
    first, second = valkey_pair
    keys = [f"rc:test:batch-race:{uuid4()}:{index}" for index in range(5)]
    owners = [{"id": "first"}, {"id": "second"}]
    barrier = asyncio.Barrier(2)

    async def attempt(store, owner) -> list[dict | None]:
        await barrier.wait()
        return await store.acquire_leases(keys, owner)

    try:
        results = await asyncio.gather(attempt(first, owners[0]), attempt(second, owners[1]))
        for column in range(len(keys)):
            verdicts = [row[column] for row in results]
            assert verdicts.count(None) == 1
            winner = owners[verdicts.index(None)]
            assert all(verdict is None or verdict == winner for verdict in verdicts)
        # A release race is equally fenced: only the matching owner can free a cell.
        releases = await asyncio.gather(
            first.release_leases(keys, owners[0]),
            second.release_leases(keys, owners[1]),
        )
        assert sorted(sum(row) for row in releases) == [0, len(keys)]
        assert not any(await first.get_leases(keys))
    finally:
        for owner in owners:
            await first.release_leases(keys, owner)
            await second.release_leases(keys, owner)


async def test_rate_limit_is_shared_and_atomic(valkey_pair) -> None:
    first, second = valkey_pair
    key = f"rc:test:rate:{uuid4()}"
    assert await first.allow_rate_limit(key, limit=2, window_seconds=60)
    assert await second.allow_rate_limit(key, limit=2, window_seconds=60)
    assert not await first.allow_rate_limit(key, limit=2, window_seconds=60)


async def test_pubsub_cross_connection(valkey_pair) -> None:
    first, second = valkey_pair
    room = f"test:{uuid4()}"
    stream = second.subscribe(room)
    receive = asyncio.create_task(anext(stream))
    try:
        # Pub/sub listeners must stay alive while a room is quiet. The command
        # client intentionally has a finite read timeout, so this also guards
        # against accidentally reusing that pool for the blocking listener.
        await asyncio.sleep(5.5)
        async with asyncio.timeout(5):
            while not receive.done():
                await first.publish(room, {"type": "test", "value": 42})
                await asyncio.sleep(0.05)
            assert await receive == {"type": "test", "value": 42}
    finally:
        if not receive.done():
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
        await stream.aclose()


async def test_presence_cross_connection(valkey_pair) -> None:
    first, second = valkey_pair
    room = f"test:{uuid4()}"
    try:
        assert not await second.refresh_presence(room, "first")
        await first.room_join(room, "first", {"name": "First"})
        assert await second.room_members(room) == {"first": {"name": "First"}}
        assert await second.refresh_presence(room, "first")
        await first.room_leave(room, "first")
        assert await second.room_members(room) == {}
    finally:
        await first.room_leave(room, "first")


async def test_undo_isolation_and_redo_invalidation(valkey_pair) -> None:
    first, second = valkey_pair
    user, session = str(uuid4()), str(uuid4())
    scope = "prices"
    try:
        await first.push_actions(user, session, scope, [{"id": 1, "undone": False}, {"id": 2, "undone": False}])
        assert await second.undo_state(user, session, scope) == (2, 0)
        assert await second.undo_state(user, "other-session", scope) == (0, 0)
        assert await second.undo_state(user, session, "movement") == (0, 0)
        await second.mark_action_undone(user, session, scope, 2, undone=True)
        assert await first.undo_state(user, session, scope) == (1, 1)
        await first.clear_redo(user, session, scope)
        assert await second.list_actions(user, session, scope) == [{"id": 1, "undone": False}]
    finally:
        await first.clear_undo_stack(user, session, scope)
