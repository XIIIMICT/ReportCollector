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
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.app_logging import log_event, log_warning
from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import User
from src.auth.service import (
    CurrentUserIdentity,
    InvalidIdentityError,
    verify_identity_ticket,
    verify_signed_identity,
)
from src.collab.events import room_key
from src.collab.store import CollabStore, get_collab_store, lease_key
from src.config import get_settings
from src.database import get_session
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
    client (full queue) drops events without blocking other room members
    (head-of-line blocking).
    """

    def __init__(self, store: CollabStore) -> None:
        self._store = store
        self._members: dict[str, dict[WebSocket, dict]] = {}
        self._queues: dict[str, dict[WebSocket, asyncio.Queue]] = {}
        self._writers: dict[str, dict[WebSocket, asyncio.Task]] = {}
        self._relay_tasks: dict[str, asyncio.Task] = {}

    async def join(self, room: str, websocket: WebSocket, member: dict) -> None:
        members = self._members.setdefault(room, {})
        if not members:
            self._relay_tasks[room] = asyncio.create_task(self._relay(room))
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._queues.setdefault(room, {})[websocket] = queue
        self._writers.setdefault(room, {})[websocket] = asyncio.create_task(self._writer(websocket, queue))
        members[websocket] = member

    async def leave(self, room: str, websocket: WebSocket) -> None:
        members = self._members.get(room)
        if members is None:
            return
        members.pop(websocket, None)
        writer = self._writers.get(room, {}).pop(websocket, None)
        if writer is not None:
            writer.cancel()
        self._queues.get(room, {}).pop(websocket, None)
        if not members:
            task = self._relay_tasks.pop(room, None)
            if task is not None:
                task.cancel()
            self._members.pop(room, None)
            self._queues.pop(room, None)
            self._writers.pop(room, None)

    async def _relay(self, room: str) -> None:
        try:
            async for message in self._store.subscribe(room):
                queues = self._queues.get(room)
                if not queues:
                    continue
                for queue in list(queues.values()):
                    if queue.full():
                        continue
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(message)
        except asyncio.CancelledError:
            return

    @staticmethod
    async def _writer(websocket: WebSocket, queue: asyncio.Queue) -> None:
        try:
            while True:
                message = await queue.get()
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.send_json(message)
        except asyncio.CancelledError:
            return


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
            client_id=websocket.query_params.get("clientId") or "",
        )
        await websocket.close(code=4401)
        return
    try:
        user, roles, assets, block = await resolve_access(session, identity)
    except NoRawMaterialsAccessError, NoRenovationAccessError:
        log_warning(
            logger,
            "collab.ws_access_rejected",
            "Отклонено WebSocket-соединение: нет доступа к проекту.",
            user_email=identity.email or "",
        )
        await websocket.close(code=4403)
        return

    origin = websocket.headers.get("origin")
    allowed_origins = get_settings().backend_cors_origins or []
    if origin is not None and allowed_origins and origin not in allowed_origins:
        log_warning(
            logger,
            "collab.ws_origin_rejected",
            "Отклонено WebSocket-соединение: неожиданный Origin.",
            user_email=user.email or "",
            extra_origin=str(origin),
        )
        await websocket.close(code=4403)
        return

    section = websocket.query_params.get("section") or default_section
    report_month = websocket.query_params.get("reportMonth") or None
    project_key = websocket.query_params.get("project") or default_project
    client_id = websocket.query_params.get("clientId") or user.email or str(user.id)
    room = room_key(project_key, section, report_month)

    session_id = websocket.query_params.get("sessionId") or client_id
    owner = {
        "userId": str(user.id),
        "name": user.name or "",
        "email": user.email or "",
        "sessionId": session_id,
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

    await websocket.accept()
    await store.room_join(room, str(user.id), owner)
    await manager.join(room, websocket, owner)
    # Undo/redo history lives in the user's stack and survives reconnects
    # (form switches); a page reload starts a new session stack. A final
    # month fix clears the stacks of the fixed table only.
    members = await store.room_members(room)
    await websocket.send_json({"type": "hello", "room": room, "user": owner, "members": members, "clientId": client_id})
    await store.publish(room, {"type": "presence:update", "members": members})
    log_event(
        logger,
        "collab.ws_connect",
        "WebSocket подключён.",
        user_email=user.email or "",
        project_key=project_key,
        room=room,
    )

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "Сообщение должно быть объектом JSON."})
                continue
            try:
                await _handle_message(
                    message,
                    websocket=websocket,
                    store=store,
                    room=room,
                    project_key=project_key,
                    owner=owner,
                    owned_leases=owned_leases,
                )
            except ValidationError as error:
                await websocket.send_json({"type": "error", "message": f"Некорректное сообщение: {error.errors()}"})
            except RedisError:
                await websocket.send_json(
                    {"type": "error", "message": "Хранилище совместного редактирования временно недоступно."}
                )
    finally:
        await _cleanup_connection(store, manager, room, project_key, websocket, owner, owned_leases)
        log_event(
            logger,
            "collab.ws_disconnect",
            "WebSocket отключён.",
            user_email=user.email or "",
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
) -> None:
    message_type = message.get("type")
    if message_type == "ping":
        refreshed = await store.refresh_presence(room, str(owner["userId"]))
        if not refreshed:
            # Presence expired while the member was idle (walked away, tab
            # hidden): they're back — online again.
            await store.room_join(room, str(owner["userId"]), owner)
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
    cells = parsed.cells[:_MAX_LEASE_CELLS]
    if message_type == "lease:acquire":
        remaining = _MAX_OWNED_LEASES - len(owned_leases)
        cells = cells[:remaining] if remaining > 0 else []
        granted: list[str] = []
        denied: list[tuple[str, dict]] = []
        for cell_key in cells:
            key = lease_key(cell_key, project=project_key)
            current_owner = await store.acquire_lease(key, owner)
            if current_owner is None:
                granted.append(cell_key)
                owned_leases.add(cell_key)
            else:
                denied.append((cell_key, current_owner))
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
        for cell_key in cells:
            key = lease_key(cell_key, project=project_key)
            released = await store.release_lease(key, owner)
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
    for cell_key in list(owned_leases):
        await store.release_lease(lease_key(cell_key, project=project_key), owner)
        await store.publish(room, {"type": "cell:unlocked", "key": cell_key, "user": owner})
    await store.room_leave(room, str(owner["userId"]))
    members = await store.room_members(room)
    await store.publish(room, {"type": "presence:update", "members": members})
    await manager.leave(room, websocket)
