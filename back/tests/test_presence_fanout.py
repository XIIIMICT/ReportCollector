"""Presence fan-out: coalesced deltas, sequenced merge input and repair snapshots."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, cast

import pytest
from test_collab import FakeWebSocket, _run_collab_ws, _wait_until

from src.collab.store import CollabStore, get_collab_store
from src.collab.ws import RoomManager, get_room_manager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


ROOM = "rm:raw_materials:materialMovement:2026-08-01"


class RecordingStore:
    """Minimal store double: record the frames a flush actually publishes."""

    def __init__(self, members: dict[str, dict] | None = None) -> None:
        self.members = members or {}
        self.published: list[dict] = []
        self.seq = 0

    async def room_members(self, _room: str) -> dict[str, dict]:
        return self.members

    async def publish_presence(self, _room: str, message: dict) -> int:
        self.seq += 1
        self.published.append({**message, "seq": self.seq})
        return self.seq


class PresenceTap:
    """Read published frames directly from a subscription — no polling."""

    def __init__(self, subscription: AsyncGenerator[dict], first: asyncio.Task) -> None:
        self.subscription = subscription
        self.first: asyncio.Task | None = first

    async def next(self) -> dict:
        if self.first is not None:
            frame = await self.first
            self.first = None
            return frame
        return await asyncio.wait_for(anext(self.subscription), timeout=2)

    async def close(self) -> None:
        await self.subscription.aclose()


async def _next_frame(subscription: AsyncIterator[dict]) -> dict:
    return await anext(subscription)


async def _tap(store: CollabStore, room: str) -> PresenceTap:
    ready = asyncio.Event()
    subscription = cast("AsyncGenerator[dict]", store.subscribe(room, ready=ready))
    first = asyncio.create_task(_next_frame(subscription))
    await asyncio.wait_for(ready.wait(), timeout=2)
    return PresenceTap(subscription, first)


async def test_join_burst_is_coalesced_into_one_delta_frame() -> None:
    """N joins in one window publish one frame with N changes, not N full maps."""
    recorder = RecordingStore()
    manager = RoomManager(cast("CollabStore", recorder))
    count = 50
    for index in range(count):
        user_id = f"u-{index:03}"
        manager.queue_presence_change(ROOM, user_id, {"userId": user_id, "name": "Имя"})

    # Nothing goes out before the flush; staged changes collapse by userId.
    assert recorder.published == []
    for index in range(count):
        manager.queue_presence_change(ROOM, f"u-{index:03}", {"userId": f"u-{index:03}", "name": "Новое имя"})
    assert await manager.flush_presence(ROOM) == 1

    assert len(recorder.published) == 1
    frame = recorder.published[0]
    assert frame["type"] == "presence:update"
    assert "members" not in frame
    assert frame["seq"] == 1
    changes = frame["changes"]
    assert len(changes) == count
    assert [change["userId"] for change in changes] == sorted(change["userId"] for change in changes)
    assert all(change["member"]["name"] == "Новое имя" for change in changes)
    assert await manager.flush_presence(ROOM) == 0


async def test_leave_is_a_tombstone_change_not_a_full_map() -> None:
    """A disconnect publishes one null-member change in the next window."""
    recorder = RecordingStore({"u-1": {"userId": "u-1"}})
    manager = RoomManager(cast("CollabStore", recorder))
    manager.queue_presence_change(ROOM, "u-1", None)
    await manager.flush_presence(ROOM)
    assert recorder.published == [{"type": "presence:update", "changes": [{"userId": "u-1", "member": None}], "seq": 1}]


async def test_join_then_leave_in_one_window_publishes_only_the_tombstone() -> None:
    """A collapsed join+leave must not resurrect a member on another worker."""
    recorder = RecordingStore()
    manager = RoomManager(cast("CollabStore", recorder))
    manager.queue_presence_change(ROOM, "u-1", {"userId": "u-1"})
    manager.queue_presence_change(ROOM, "u-1", None)
    assert await manager.flush_presence(ROOM) == 1
    assert recorder.published == [{"type": "presence:update", "changes": [{"userId": "u-1", "member": None}], "seq": 1}]


async def test_ping_alone_publishes_nothing_and_repair_publishes_full_members() -> None:
    """Heartbeats are silent; a due repair frame remains a full snapshot."""
    members = {"u-1": {"userId": "u-1"}, "u-2": {"userId": "u-2"}}
    recorder = RecordingStore(members)
    manager = RoomManager(cast("CollabStore", recorder))
    # Force the interval boundary deterministically instead of waiting 15 s.
    manager._last_full[ROOM] = 0.0
    manager.note_presence_ping(ROOM)
    assert await manager.flush_presence(ROOM) == 1
    frame = recorder.published[0]
    assert frame["type"] == "presence:update"
    assert frame["members"] == members
    assert "changes" not in frame
    assert frame["seq"] == 1

    # No change and no due repair: a heartbeat is just a pong.
    manager.note_presence_ping(ROOM)
    assert await manager.flush_presence(ROOM) == 0


async def test_publish_presence_sequences_are_strictly_increasing(isolated_store: CollabStore) -> None:
    """Both stores hand out monotonic seqs; subscribers see the seq field."""
    seqs = [await isolated_store.publish_presence(ROOM, {"type": "presence:update", "members": {}}) for _ in range(3)]
    assert len(set(seqs)) == 3
    assert seqs == sorted(seqs)

    tap = await _tap(isolated_store, ROOM)
    try:
        seq = await isolated_store.publish_presence(ROOM, {"type": "presence:update", "changes": []})
        frame = await tap.next()
        assert frame["seq"] == seq
        assert json.loads(json.dumps(frame))["type"] == "presence:update"
    finally:
        await tap.close()


@pytest.mark.real_schema
async def test_hello_resync_is_unchanged_and_leave_is_a_delta(db_session: AsyncSession) -> None:
    """A reconnect still gets the exact full hello; joins/leaves travel as deltas."""
    manager = get_room_manager()
    store = get_collab_store()
    tap = await _tap(store, ROOM)
    first = FakeWebSocket(project="raw_materials")
    first.query_params["clientId"] = "tab-1"
    second = FakeWebSocket(project="raw_materials")
    second.query_params["clientId"] = "tab-2"
    tasks: list[asyncio.Task] = []
    try:
        tasks.append(await _run_collab_ws(db_session, first))
        await _wait_until(lambda: any(message.get("type") == "hello" for message in first.sent))
        hello = next(message for message in first.sent if message["type"] == "hello")
        assert set(hello) == {"type", "room", "user", "members", "clientId"}
        assert hello["room"] == ROOM
        assert hello["clientId"] == "tab-1"
        present = set(hello["members"])
        assert hello["user"]["userId"] in present

        tasks.append(await _run_collab_ws(db_session, second))
        await _wait_until(lambda: any(message.get("type") == "hello" for message in second.sent))
        resync = next(message for message in second.sent if message["type"] == "hello")
        assert set(resync) == set(hello)
        assert resync["clientId"] == "tab-2"
        assert set(resync["members"]) == present

        assert await manager.flush_presence(ROOM) == 1
        joined = await tap.next()
        assert "changes" in joined
        assert "members" not in joined
        assert joined["changes"][0]["userId"] == hello["user"]["userId"]
        assert joined["seq"] > 0

        second.disconnect = True
        await tasks[1]
        tasks.pop()
        assert await manager.flush_presence(ROOM) == 1
        left = await tap.next()
        assert left["changes"] == [{"userId": hello["user"]["userId"], "member": None}]
        assert left["seq"] > joined["seq"]
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with pytest.raises(asyncio.CancelledError):
                await task
        # Drain any change staged during cancellation before the store fixture
        # closes, otherwise the timer fires inside the next test.
        await manager.flush_presence(ROOM)
        await tap.close()


@pytest.mark.real_schema
async def test_ping_keeps_lease_and_hello_paths_untouched(db_session: AsyncSession) -> None:
    """A heartbeat still refreshes TTL and answers pong without a broadcast."""
    manager = get_room_manager()
    fake = FakeWebSocket(project="raw_materials")
    tap = await _tap(get_collab_store(), ROOM)
    task = await _run_collab_ws(db_session, fake)
    try:
        await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))
        await manager.flush_presence(ROOM)
        await tap.next()  # the join delta
        fake.incoming.append({"type": "ping"})
        await _wait_until(lambda: any(message.get("type") == "pong" for message in fake.sent))
        # A refresh-only heartbeat stages no broadcast at all.
        assert await manager.flush_presence(ROOM) == 0
    finally:
        fake.disconnect = True
        await task
        await manager.flush_presence(ROOM)
        await tap.close()
