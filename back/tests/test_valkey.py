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


async def test_pubsub_cross_connection(valkey_pair) -> None:
    first, second = valkey_pair
    room = f"test:{uuid4()}"
    stream = second.subscribe(room)
    receive = asyncio.create_task(anext(stream))
    try:
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
