"""Run the same behavioral contract in both CI store jobs."""

import asyncio

import pytest


@pytest.fixture
def store(isolated_store):
    """Exercise the transport contract, not the transactional journal adapter."""
    return isolated_store


async def test_bulk_actions_keep_caller_order_and_redo_oldest_first(store) -> None:
    actions = [{"id": value, "undone": False} for value in [3, 2, 1]]
    await store.push_actions("user", "session", "scope", actions)
    assert await store.list_actions("user", "session", "scope") == actions
    assert (await store.next_action("user", "session", "scope", undone=False))["id"] == 3
    for value in [3, 2, 1]:
        await store.mark_action_undone("user", "session", "scope", value, undone=True)
    assert (await store.next_action("user", "session", "scope", undone=True))["id"] == 1
    assert (await store.next_action("user", "session", "scope", undone=True, after_action_id=1))["id"] == 2


async def test_concurrent_lease_acquisition_has_one_owner(store) -> None:
    owners = [{"userId": str(index)} for index in range(20)]
    results = await asyncio.gather(*(store.acquire_lease("contract:lease", owner) for owner in owners))
    assert results.count(None) == 1
    winner = owners[results.index(None)]
    assert await store.get_lease("contract:lease") == winner
    assert all(result is None or result == winner for result in results)
    assert await store.release_lease("contract:lease", winner)


async def test_cell_revision_is_atomic_and_numeric(store) -> None:
    revisions = await asyncio.gather(
        *(
            store.bump_cell_state("contract/cell", user_id="user", user_name="User", action_id=index)
            for index in range(20)
        )
    )
    assert sorted(revisions) == list(range(1, 21))
    assert (await store.get_cell_state("contract/cell"))["revision"] == 20


async def test_active_member_does_not_keep_an_idle_member_online(store, monkeypatch) -> None:
    monkeypatch.setattr("src.collab.store.PRESENCE_TTL_SECONDS", 1)
    await store.room_join("contract:presence", "idle", {"name": "Idle"})
    await asyncio.sleep(0.6)
    await store.room_join("contract:presence", "active", {"name": "Active"})
    await asyncio.sleep(0.6)
    assert await store.refresh_presence("contract:presence", "active")
    assert not await store.refresh_presence("contract:presence", "idle")
    assert await store.room_members("contract:presence") == {"active": {"name": "Active"}}


async def test_old_connection_cleanup_does_not_remove_new_presence(store) -> None:
    await store.room_join("contract:takeover", "user", {"clientId": "old"})
    await store.room_join("contract:takeover", "user", {"clientId": "new"})
    await store.room_leave("contract:takeover", "user", client_id="old")
    assert await store.room_members("contract:takeover") == {"user": {"clientId": "new"}}
    await store.room_leave("contract:takeover", "user", client_id="new")
    assert await store.room_members("contract:takeover") == {}
