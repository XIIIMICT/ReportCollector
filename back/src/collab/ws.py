"""
Collaboration WebSocket: /api/ws/raw-materials and /api/ws/renovation.

The connection is authorized by the identity signature (the same
X-ReportCollector-* headers as REST). The client names the room in the
query (project, section, reportMonth) and receives: edit events of other
users, cell leases, presence and heartbeat.

The project isolates rooms, leases and presence: edits of different
projects don't intersect. Lease keys in the store are scoped by project
(lease_key(cell_key, project=...)), while cell:locked/unlocked publications
carry the original canonical key — the client-side remoteLocks doesn't
depend on the project. Room events fan out via the store's pub/sub: REST
edit events and other workers' events are relayed to all room connections
through a per-socket queue (a slow client doesn't block the room).
"""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocketDisconnected
from valkey.exceptions import ValkeyError

from src.app_logging import log_event, log_failure, log_warning
from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import User
from src.auth.service import (
    CurrentUserIdentity,
    InvalidIdentityError,
    verify_identity_ticket,
    verify_signed_identity,
)
from src.collab.access import RoomAccess, RoomAccessDeniedError
from src.collab.events import room_key
from src.collab.locking import lock_project, lock_rows
from src.collab.store import CollabStore, get_collab_store, lease_key
from src.config import get_settings
from src.database import get_session
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.service import NoKppkAccessError, resolve_kppk_access
from src.logistics.constants import LOGISTICS_PROJECT_KEY
from src.logistics.service import NoLogisticsAccessError, resolve_logistics_access
from src.metrics import (
    WS_ACCESS_CHECK_DURATION,
    WS_CONNECTIONS_ACTIVE,
    WS_DISCONNECTS_TOTAL,
    WS_EVENTS_RELAYED_TOTAL,
    WS_MESSAGES_TOTAL,
    WS_QUEUE_DEPTH,
    WS_ROOM_MEMBERS,
    WS_ROOMS_ACTIVE,
    WS_SLOW_CONSUMERS_TOTAL,
    WS_WRITER_LAG,
)
from src.raw_materials.service import NoRawMaterialsAccessError, resolve_raw_materials_access
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RBlock
from src.renovation.service import NoRenovationAccessError, resolve_renovation_access

router = APIRouter(tags=["collab"])

logger = logging.getLogger("reportcollector")

SessionDependency = Annotated[AsyncSession, Depends(get_session)]

_MAX_LEASE_CELLS = 500
_MAX_OWNED_LEASES = 1000  # max leases per connection
_QUEUE_SIZE = 256  # outbound message queue size per connection
_MAX_MESSAGE_BYTES = 64 * 1024
_MESSAGE_RATE_WINDOW = 10.0
_MAX_MESSAGES_PER_WINDOW = 300

# Resolves (user, roles, assets, block) for the connection's project.
AccessResolver = Callable[
    [AsyncSession, CurrentUserIdentity],
    Awaitable[tuple[User, list[Any], list[Any], RBlock | None]],
]


class LeaseMessage(BaseModel):
    type: Literal["lease:acquire", "lease:renew", "lease:release"]
    cells: list[str] = []


class PresenceMessage(BaseModel):
    type: Literal["presence"]
    selection: dict[str, object] | None = None


class PingMessage(BaseModel):
    type: Literal["ping"]


class RoomManager:
    """
    Local per-connection room subscriptions + pub/sub event relay.

    Each connection gets a bounded queue and a dedicated writer task: a slow
    client (full queue) reconnects and refetches authoritative state instead
    of silently losing individual events.
    """

    def __init__(self, store: CollabStore) -> None:
        self._store = store
        self._members: dict[str, dict[WebSocket, dict]] = {}
        self._queues: dict[str, dict[WebSocket, asyncio.Queue]] = {}
        self._writers: dict[str, dict[WebSocket, asyncio.Task]] = {}
        self._relay_tasks: dict[str, asyncio.Task] = {}
        self._access: dict[WebSocket, RoomAccess] = {}
        self._ready: dict[str, asyncio.Event] = {}
        self._close_reasons: dict[WebSocket, str] = {}

    async def join(self, room: str, websocket: WebSocket, member: dict, access: RoomAccess | None = None) -> None:
        members = self._members.setdefault(room, {})
        new_room = not members
        if new_room:
            self._ready[room] = asyncio.Event()
            self._relay_tasks[room] = asyncio.create_task(self._relay(room))
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        if access is not None:
            self._access[websocket] = access
        self._queues.setdefault(room, {})[websocket] = queue
        self._writers.setdefault(room, {})[websocket] = asyncio.create_task(self._writer(room, websocket, queue))
        members[websocket] = member
        # Gauges track the LIVE state, so they move with every join/leave — a
        # scrape reads whatever the last lifecycle event left behind.
        WS_CONNECTIONS_ACTIVE.inc()
        if new_room:
            WS_ROOMS_ACTIVE.inc()
        section = self._room_section(room)
        WS_ROOM_MEMBERS.labels(room_section=section).inc()
        await asyncio.wait_for(self._ready[room].wait(), timeout=10)
        if self._relay_tasks[room].done():
            error = "Room subscription failed"
            raise ValkeyError(error)

    async def leave(self, room: str, websocket: WebSocket) -> None:
        members = self._members.get(room)
        if members is None:
            return
        if websocket in members:
            WS_CONNECTIONS_ACTIVE.dec()
            WS_ROOM_MEMBERS.labels(room_section=self._room_section(room)).dec()
            WS_DISCONNECTS_TOTAL.labels(
                room_section=self._room_section(room), reason=self._close_reasons.pop(websocket, "client")
            ).inc()
        members.pop(websocket, None)
        self._access.pop(websocket, None)
        writer = self._writers.get(room, {}).pop(websocket, None)
        if writer is not None:
            writer.cancel()
            with suppress(asyncio.CancelledError):
                await writer
        removed = self._queues.get(room, {}).pop(websocket, None)
        if removed is not None:
            WS_QUEUE_DEPTH.labels(room_section=self._room_section(room)).dec(removed.qsize())
        if not members:
            WS_ROOMS_ACTIVE.dec()
            task = self._relay_tasks.pop(room, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._members.pop(room, None)
            self._queues.pop(room, None)
            self._writers.pop(room, None)
            self._ready.pop(room, None)

    @staticmethod
    def _room_section(room: str) -> str:
        """
        Return the room's section part for metric labels (`rawMaterials`, `kppkCost`…).

        The room key is `{project}:{section}:{reportMonth}:{scope}`; the month and
        the ДЗО scope are dropped so the label set stays small and stable — one
        series per form, not one per month times per asset.
        """
        parts = room.split(":")
        return parts[1] if len(parts) > 1 else "unknown"

    async def _relay(self, room: str) -> None:
        section = self._room_section(room)
        try:
            async for message in self._store.subscribe(room, ready=self._ready[room]):
                if message.get("type") == "transport:resync":
                    break
                queues = self._queues.get(room)
                if not queues:
                    continue
                for websocket, queue in list(queues.items()):
                    if queue.full():
                        # The client cannot keep up and is dropped ON PURPOSE: it
                        # reconnects and refetches. Nothing fails, so only this
                        # counter can reveal a rising rate.
                        WS_SLOW_CONSUMERS_TOTAL.labels(room_section=section).inc()
                        await self.close(websocket, 1013, "slow_consumer")
                        continue
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait((time.monotonic(), message))
                        WS_QUEUE_DEPTH.labels(room_section=section).inc()
                        WS_EVENTS_RELAYED_TOTAL.labels(room_section=section).inc()
        except asyncio.CancelledError:
            return
        except Exception as error:  # noqa: BLE001 - classify, never log exception text
            log_failure(
                logger,
                "collab.ws_relay_failed",
                "Collaboration relay failed; clients must reconnect and resynchronize",
                error,
                error_code="ws_relay_failed",
                phase="relay",
            )
        # Exhaustion is also a lost subscription, not a healthy connection.
        for websocket in list(self._queues.get(room, {})):
            await self.close(websocket, 1013, "transport")
        ready = self._ready.get(room)
        if ready is not None:
            ready.set()

    async def close(self, websocket: WebSocket, code: int, reason: str) -> None:
        self._close_reasons.setdefault(websocket, reason)
        with suppress(RuntimeError, WebSocketDisconnect, WebSocketDisconnected):
            await websocket.close(code=code)

    async def _writer(self, room: str, websocket: WebSocket, queue: asyncio.Queue) -> None:
        section = self._room_section(room)
        try:
            while True:
                queued = [await queue.get()]
                while not queue.empty() and len(queued) < _QUEUE_SIZE:
                    queued.append(queue.get_nowait())
                WS_QUEUE_DEPTH.labels(room_section=section).dec(len(queued))
                messages = [item[1] for item in queued]
                access_wait = time.monotonic() - queued[0][0]
                access = self._access.get(websocket)
                WS_WRITER_LAG.labels(room_section=section).observe(access_wait)
                if access is not None:
                    with WS_ACCESS_CHECK_DURATION.labels(room_section=section).time():
                        messages = await access.filter_messages(messages)
                for message in messages:
                    await websocket.send_json(message)
        except asyncio.CancelledError:
            return
        except (
            RoomAccessDeniedError,
            NoRawMaterialsAccessError,
            NoRenovationAccessError,
            NoKppkAccessError,
            NoLogisticsAccessError,
        ):
            await self.close(websocket, 4403, "access_denied")
        except Exception as error:  # noqa: BLE001 - classify, never log exception text
            log_failure(
                logger,
                "collab.ws_writer_failed",
                "Collaboration writer failed; closing connection",
                error,
                error_code="ws_writer_failed",
                phase="writer",
            )
            await self.close(websocket, 1013, "writer_error")


@lru_cache(maxsize=1)
def get_room_manager() -> RoomManager:
    return RoomManager(get_collab_store())


@router.websocket("/ws/raw-materials")
async def raw_materials_ws(
    websocket: WebSocket,
    session: SessionDependency,
) -> None:
    """Collaboration room for the «Движение» and «Цены» forms."""
    await _serve_room(
        websocket,
        session,
        resolve_access=_resolve_raw_materials_access,
        default_section="materialMovement",
        default_project=RAW_MATERIALS_PROJECT_KEY,
    )


@router.websocket("/ws/renovation")
async def renovation_ws(
    websocket: WebSocket,
    session: SessionDependency,
) -> None:
    """Collaboration room for the «Ремонт» plan form."""
    await _serve_room(
        websocket,
        session,
        resolve_access=_resolve_renovation_access,
        default_section="renovationPlan",
        default_project=RENOVATION_PROJECT_KEY,
    )


@router.websocket("/ws/kppk")
async def kppk_ws(
    websocket: WebSocket,
    session: SessionDependency,
) -> None:
    """Collaboration room for the «КППК» dictionaries (admin-edited)."""
    await _serve_room(
        websocket,
        session,
        resolve_access=_resolve_kppk_access,
        default_section="kppkPositions",
        default_project=KPPK_PROJECT_KEY,
    )


@router.websocket("/ws/logistics")
async def logistics_ws(
    websocket: WebSocket,
    session: SessionDependency,
) -> None:
    """Collaboration room for the «Логистика» «Ставки» form (admin-only)."""
    await _serve_room(
        websocket,
        session,
        resolve_access=_resolve_logistics_access,
        default_section="logisticsRates",
        default_project=LOGISTICS_PROJECT_KEY,
    )


async def _resolve_raw_materials_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[Any], list[Any], None]:
    """Raw-materials access in the shared (user, roles, assets, block) shape."""
    user, roles, assets = await resolve_raw_materials_access(session, identity)
    return user, roles, assets, None


async def _resolve_renovation_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[Any], list[Any], RBlock | None]:
    return await resolve_renovation_access(session, identity)


async def _resolve_kppk_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[Any], list[Any], None]:
    """КППК access: no per-asset grants, every member reads the dictionaries."""
    user, roles = await resolve_kppk_access(session, identity)
    return user, roles, [], None


async def _resolve_logistics_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[Any], list[Any], None]:
    """«Логистика» access: any working member; a worker brings their asset ids."""
    membership = await resolve_logistics_access(session, identity)
    return membership.user, [membership.role], sorted(membership.asset_ids), None


async def _serve_room(
    websocket: WebSocket,
    session: AsyncSession,
    *,
    resolve_access: AccessResolver,
    default_section: str,
    default_project: str,
) -> None:
    """Run a common collaboration room lifecycle for a project's WebSocket."""
    identity = None
    try:
        identity = verify_signed_identity(websocket.headers)
    except InvalidIdentityError:
        ticket = websocket.query_params.get("ticket")
        if ticket is not None:
            try:
                identity = verify_identity_ticket(ticket)
            except InvalidIdentityError:
                identity = None
    if identity is None:
        log_warning(
            logger,
            "collab.ws_identity_rejected",
            "Отклонено WebSocket-соединение: не удалось проверить идентичность.",
        )
        await websocket.close(code=4401)
        return
    section = websocket.query_params.get("section") or default_section
    report_month = websocket.query_params.get("reportMonth") or None
    scope = websocket.query_params.get("scope") or None
    project_key = websocket.query_params.get("project") or default_project
    if project_key != default_project:
        await websocket.close(code=4403)
        return
    try:
        access = RoomAccess(
            lambda: AsyncSession(session.bind, expire_on_commit=False),
            identity,
            resolve_access,
            project_key,
            section,
            report_month,
            scope,
        )
        user, roles, assets, block = await access.resolve(session)
    except (
        RoomAccessDeniedError,
        NoRawMaterialsAccessError,
        NoRenovationAccessError,
        NoKppkAccessError,
        NoLogisticsAccessError,
    ):
        log_warning(
            logger,
            "collab.ws_access_rejected",
            "Отклонено WebSocket-соединение: нет доступа к проекту.",
        )
        await websocket.close(code=4403)
        return
    finally:
        # A dependency lives for the entire socket. Release its read transaction
        # now; subsequent checks use independent, short-lived sessions.
        await session.close()

    origin = websocket.headers.get("origin")
    allowed_origins = get_settings().backend_cors_origins or []
    if origin is not None and allowed_origins and origin not in allowed_origins:
        log_warning(
            logger,
            "collab.ws_origin_rejected",
            "Отклонено WebSocket-соединение: неожиданный Origin.",
        )
        await websocket.close(code=4403)
        return

    client_id = websocket.query_params.get("clientId") or user.email or str(user.id)
    room = room_key(project_key, section, report_month, scope)

    session_id = websocket.query_params.get("sessionId") or client_id
    owner = {
        "userId": str(user.id),
        "name": user.name or "",
        "email": user.email or "",
        "sessionId": session_id,
        "clientId": client_id,
        "assets": [asset.name for asset in assets],
        # Raw role value («admin»/«asset_worker»/...): the client decides
        # whether to show the role or the asset list.
        "role": roles[0].value if roles else "",
    }
    if block is not None:
        owner["block"] = block.value
    store = get_collab_store()
    manager = get_room_manager()
    owned_leases: set[str] = set()
    message_times: deque[float] = deque()

    try:
        await websocket.accept()
        try:
            await store.room_join(room, str(user.id), owner)
            await manager.join(room, websocket, owner, access)
        except ValkeyError, TimeoutError:
            await manager.leave(room, websocket)
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=1013)
            return
        # Undo/redo history lives in the user's stack and survives reconnects
        # (form switches); a page reload starts a new session stack. A final
        # month fix clears the stacks of the fixed table only.
        members = await store.room_members(room)
        hello = {"type": "hello", "room": room, "user": owner, "members": members, "clientId": client_id}
        for message in await access.filter_messages([hello]):
            await websocket.send_json(message)
        await store.publish(room, {"type": "presence:update", "members": members})
        log_event(
            logger,
            "collab.ws_connect",
            "WebSocket подключён.",
            project_key=project_key,
            room=room,
        )

    except WebSocketDisconnect, WebSocketDisconnected:
        # A client may disappear during the handshake, before Starlette has
        # moved the socket into CONNECTED state.  Treat that race as a normal
        # disconnect; attempting to read or close it again only creates a
        # misleading 500 in the ASGI server log.
        with suppress(ValkeyError, RuntimeError, WebSocketDisconnect, WebSocketDisconnected):
            await _cleanup_connection(store, manager, room, project_key, websocket, owner, owned_leases)
        return
    except BaseException:
        with suppress(ValkeyError):
            await _cleanup_connection(store, manager, room, project_key, websocket, owner, owned_leases)
        raise

    try:
        while True:
            try:
                frame = await websocket.receive()
                if frame.get("type") == "websocket.disconnect":
                    break
                raw_bytes = frame.get("bytes")
                if raw_bytes is not None:
                    if len(raw_bytes) > _MAX_MESSAGE_BYTES:
                        await manager.close(websocket, 1009, "message_too_large")
                        break
                    try:
                        raw = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        await manager.close(websocket, 1007, "invalid_utf8")
                        break
                else:
                    raw = str(frame.get("text", ""))
            except WebSocketDisconnect, WebSocketDisconnected:
                break
            now = time.monotonic()
            while message_times and now - message_times[0] > _MESSAGE_RATE_WINDOW:
                message_times.popleft()
            message_bytes = len(raw.encode("utf-8"))
            section = room.split(":")[1] if ":" in room else "unknown"
            if message_bytes > _MAX_MESSAGE_BYTES:
                await manager.close(websocket, 1009, "message_too_large")
                break
            if len(message_times) >= _MAX_MESSAGES_PER_WINDOW:
                await manager.close(websocket, 1008, "rate_limit")
                break
            message_times.append(now)
            try:
                message = json.loads(raw)
            except ValueError, RecursionError:
                await manager.close(websocket, 1007, "invalid_json")
                break
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "Сообщение должно быть объектом JSON."})
                continue
            message_type = message.get("type")
            WS_MESSAGES_TOTAL.labels(
                room_section=room.split(":")[1] if ":" in room else "unknown",
                message_type=message_type
                if isinstance(message_type, str)
                and message_type in {"ping", "presence", "lease:acquire", "lease:renew", "lease:release"}
                else "invalid",
            ).inc()
            try:
                await _handle_message(
                    message,
                    websocket=websocket,
                    store=store,
                    room=room,
                    project_key=project_key,
                    owner=owner,
                    owned_leases=owned_leases,
                    access=access,
                )
            except WebSocketDisconnect, WebSocketDisconnected:
                # The browser can close immediately after sending its final
                # heartbeat.  Starlette reports a receive-side disconnect as
                # ``WebSocketDisconnect`` and a concurrent send as the
                # runtime ``WebSocketDisconnected``; both are normal socket
                # lifecycle events and must not bubble into Granian's error
                # middleware as application failures.
                break
            except (
                RoomAccessDeniedError,
                NoRawMaterialsAccessError,
                NoRenovationAccessError,
                NoKppkAccessError,
                NoLogisticsAccessError,
            ):
                with suppress(RuntimeError, WebSocketDisconnect, WebSocketDisconnected):
                    await manager.close(websocket, 4403, "access_denied")
                break
            except ValidationError as error:
                await websocket.send_json({"type": "error", "message": f"Некорректное сообщение: {error.errors()}"})
            except ValkeyError:
                await websocket.send_json(
                    {"type": "error", "message": "Хранилище совместного редактирования временно недоступно."}
                )
    finally:
        with suppress(ValkeyError):
            await _cleanup_connection(store, manager, room, project_key, websocket, owner, owned_leases)
        log_event(
            logger,
            "collab.ws_disconnect",
            "WebSocket отключён.",
            project_key=project_key,
            room=room,
        )


async def _handle_message(
    message: dict,
    *,
    websocket: WebSocket,
    store: CollabStore,
    room: str,
    project_key: str,
    owner: dict,
    owned_leases: set[str],
    access: RoomAccess | None = None,
) -> None:
    message_type = message.get("type")
    # Only leases and a presence with a selection need an access check; a bare
    # `ping` or a selection-less `presence` must not open a session and re-run
    # access resolution on every heartbeat (AUD-09).
    needs_access = message_type in {"lease:acquire", "lease:renew"} or (
        message_type == "presence" and message.get("selection") is not None
    )
    if access is not None and needs_access:
        async with access.session_factory() as check_session:
            if message_type in {"lease:acquire", "lease:renew"}:
                await lock_project(check_session, project_key)
            current_access = await access.resolve(check_session)
            if message_type in {"lease:acquire", "lease:renew"}:
                parsed = LeaseMessage.model_validate(message)
                # Phase 9: an oversized selection is an explicit error, never
                # a silent slice — the client must know what was not leased.
                if len(parsed.cells) > _MAX_LEASE_CELLS:
                    await websocket.send_json(
                        {"type": "error", "message": f"Слишком много ячеек для аренды (не более {_MAX_LEASE_CELLS})."}
                    )
                    return
                requested = parsed.cells
                await lock_rows(check_session, [key.rsplit("/", 1)[0] for key in requested])
                allowed = await access.cells(check_session, current_access, requested, write=True)
                if any(key not in allowed for key in requested):
                    await websocket.send_json({"type": "error", "message": "Нет доступа к выбранным ячейкам."})
                    return
                # Keep the shared lifecycle/row locks until Valkey acquisition
                # finishes; a finalization cannot slip between check and lease.
                await _handle_message(
                    message,
                    websocket=websocket,
                    store=store,
                    room=room,
                    project_key=project_key,
                    owner=owner,
                    owned_leases=owned_leases,
                )
                return
            if message_type == "presence":
                parsed_presence = PresenceMessage.model_validate(message)
                selection = parsed_presence.selection
                if selection is not None:
                    keys = selection.get("cellKeys")
                    if (
                        not isinstance(keys, list)
                        or len(keys) > _MAX_OWNED_LEASES
                        or not all(isinstance(key, str) for key in keys)
                    ):
                        await websocket.send_json({"type": "error", "message": "Некорректное выделение."})
                        return
                    allowed = await access.cells(check_session, current_access, keys, write=False)
                    if any(key not in allowed for key in keys):
                        await websocket.send_json({"type": "error", "message": "Нет доступа к выбранным ячейкам."})
                        return
    if message_type == "ping":
        refreshed = await store.refresh_presence(room, str(owner["userId"]))
        if not refreshed:
            # Presence expired while the member was idle (walked away, tab
            # hidden): they're back — online again.
            await store.room_join(room, str(owner["userId"]), owner)
        # Reconcile missed pub/sub snapshots and prune members whose own TTL
        # expired, even when this member's heartbeat only renewed its entry.
        members = await store.room_members(room)
        await store.publish(room, {"type": "presence:update", "members": members})
        await websocket.send_json({"type": "pong"})
        return
    if message_type == "presence":
        parsed = PresenceMessage.model_validate(message)
        # Re-store the member's presence so the selection highlight clears
        # immediately for others (e.g. after Esc), not only when the presence
        # TTL expires. The selection field is included only when non-null.
        payload = {**owner}
        if parsed.selection is not None:
            payload["selection"] = parsed.selection
        await store.room_join(room, str(owner["userId"]), payload)
        members = await store.room_members(room)
        await store.publish(room, {"type": "presence:update", "members": members})
        return
    parsed = LeaseMessage.model_validate(message)
    cells = parsed.cells
    if len(cells) > _MAX_LEASE_CELLS:
        await websocket.send_json(
            {"type": "error", "message": f"Слишком много ячеек для аренды (не более {_MAX_LEASE_CELLS})."}
        )
        return
    if message_type == "lease:acquire":
        remaining = _MAX_OWNED_LEASES - len(owned_leases)
        if len(cells) > remaining:
            await websocket.send_json(
                {"type": "error", "message": f"Слишком много выделенных ячеек (не более {_MAX_OWNED_LEASES})."}
            )
            return
        # One EVALSHA for the whole message. Partial grants stay visible: each
        # cell keeps its own granted/denied verdict and cell:locked publication.
        keys = [lease_key(cell_key, project=project_key) for cell_key in cells]
        verdicts = await store.acquire_leases(keys, owner)
        granted = [cell for cell, verdict in zip(cells, verdicts, strict=True) if verdict is None]
        denied = [(cell, verdict) for cell, verdict in zip(cells, verdicts, strict=True) if verdict is not None]
        owned_leases.update(granted)
        await websocket.send_json({"type": "lease:granted", "cells": granted, "user": owner})
        if denied:
            await websocket.send_json(
                {
                    "type": "lease:denied",
                    "cells": [{"key": key, "user": current_owner} for key, current_owner in denied],
                }
            )
        for cell_key in granted:
            await store.publish(room, {"type": "cell:locked", "key": cell_key, "user": owner})
        return
    if message_type == "lease:renew":
        for cell_key in cells:
            await store.renew_lease(lease_key(cell_key, project=project_key), owner)
        return
    if message_type == "lease:release":
        keys = [lease_key(cell_key, project=project_key) for cell_key in cells]
        verdicts = await store.release_leases(keys, owner)
        for cell_key, released in zip(cells, verdicts, strict=True):
            owned_leases.discard(cell_key)
            if released:
                await store.publish(room, {"type": "cell:unlocked", "key": cell_key, "user": owner})


async def _cleanup_connection(
    store: CollabStore,
    manager: RoomManager,
    room: str,
    project_key: str,
    websocket: WebSocket,
    owner: dict,
    owned_leases: set[str],
) -> None:
    # Always cancel local tasks, including when Valkey is unavailable.
    await manager.leave(room, websocket)
    for cell_key in list(owned_leases):
        await store.release_lease(lease_key(cell_key, project=project_key), owner)
        await store.publish(room, {"type": "cell:unlocked", "key": cell_key, "user": owner})
    await store.room_leave(room, str(owner["userId"]), client_id=owner.get("clientId"))
    members = await store.room_members(room)
    await store.publish(room, {"type": "presence:update", "members": members})
