"""Совместное редактирование: журнал правок, undo/redo, конфликты и аренда ячеек."""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from conftest import signed_identity_headers
from fastapi import WebSocketDisconnect
from sqlmodel import col, select
from starlette.datastructures import Headers
from test_raw_materials import admin_headers

if TYPE_CHECKING:
    from fastapi import WebSocket

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User
from src.auth.service import (
    InvalidIdentityError,
    activate_project_user,
    grant_project_role,
    verify_identity_ticket,
)
from src.collab.store import lease_key
from src.collab.ws import raw_materials_ws
from src.raw_materials.models import MaterialMovement


async def _patch_movement(client, row_id: str, field: str, value: float) -> dict:
    response = await client.patch(
        "/api/raw-materials/movement",
        headers=signed_identity_headers(**admin_headers()),
        json={"reportMonth": "2026-08-01", "updates": [{"rowId": row_id, "field": field, "value": value}]},
    )
    assert response.status_code == 200
    return response.json()


async def _movement_value(client, row_id: str, field: str) -> float | None:
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()
    return next(row for row in rows if row["id"] == row_id)[field]


async def _post_undo(client, *, skip_action_id: int | None = None) -> dict:
    response = await client.post(
        "/api/raw-materials/undo",
        headers=signed_identity_headers(**admin_headers()),
        json={"skipActionId": skip_action_id},
    )
    assert response.status_code == 200
    return response.json()


async def _post_redo(client) -> dict:
    response = await client.post("/api/raw-materials/redo", headers=signed_identity_headers(**admin_headers()))
    assert response.status_code == 200
    return response.json()


def _identity_ticket(headers: dict[str, str]) -> str:
    """Тикет WebSocket в формате фронтенда (route-handler /api/collab/ticket)."""
    ticket = {
        "h": {
            name: headers.get(name, "")
            for name in (
                "X-ReportCollector-User-Key",
                "X-ReportCollector-User-Email",
                "X-ReportCollector-User-Name",
                "X-ReportCollector-User-Sub",
                "X-ReportCollector-User-Issuer",
                "X-ReportCollector-Preferred-Username",
                "X-ReportCollector-Given-Name",
                "X-ReportCollector-Family-Name",
                "X-ReportCollector-Email-Verified",
                "X-ReportCollector-Identity-Timestamp",
                "X-ReportCollector-Identity-Signature",
            )
        }
    }
    return base64.urlsafe_b64encode(json.dumps(ticket).encode("utf-8")).decode("ascii")


async def test_ws_ticket_verifies_admin_identity() -> None:
    identity = verify_identity_ticket(_identity_ticket(signed_identity_headers(**admin_headers())))
    assert identity.email == "admin@example.local"
    assert identity.key == "admin"
    assert identity.oidc_sub == "admin-sub"


async def test_ws_ticket_rejects_forged_signature() -> None:
    headers = signed_identity_headers(**admin_headers())
    headers["X-ReportCollector-Identity-Signature"] = "0" * 64
    with pytest.raises(InvalidIdentityError):
        verify_identity_ticket(_identity_ticket(headers))


async def test_ws_ticket_rejects_stale_timestamp() -> None:
    headers = signed_identity_headers(**admin_headers())
    headers["X-ReportCollector-Identity-Timestamp"] = str(int(datetime.now(UTC).timestamp()) - 300)
    with pytest.raises(InvalidIdentityError):
        verify_identity_ticket(_identity_ticket(headers))


@pytest.fixture(autouse=True)
async def restore_movement_seed(db_session) -> AsyncGenerator[None]:
    """Возвращает строки «Движения» за август в сид-состояние (не портить другие тесты)."""
    yield
    rows = (
        await db_session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == date(2026, 8, 1)))
    ).all()
    for row in rows:
        row.balance_on_site_tons = 5.0
        row.updated_by = ""
        row.updated_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.add(row)
    await db_session.commit()


async def _movement_row_id(client) -> str:
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()
    filled = next(row for row in rows if row["assetName"] == "СНХЗ" and row["rawMaterialName"] == "Аммиак")
    return filled["id"]


async def _make_project_user(db_session, *, email: str, name: str, sub: str) -> User:
    """Пользователь проекта «Сырьё» с ролью asset_worker (для тестов от чужого имени)."""
    now = datetime.now(UTC)
    user = User(key=email, email=email, name=name, created_at=now, updated_at=now)
    db_session.add(user)
    await db_session.flush()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await db_session.commit()
    return user


async def test_edit_writes_journal_and_cell_state(client, db_session, store) -> None:
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    admin = (await db_session.exec(select(User).where(col(User.email) == "admin@example.local"))).one()
    actions = await store.list_actions(str(admin.id))
    assert len(actions) == 1
    action = actions[0]
    assert action["targetTable"] == "rm_material_movement"
    assert action["rowId"] == row_id
    assert action["field"] == "balanceOnSiteTons"
    assert action["oldValue"] == 5.0
    assert action["newValue"] == 12.5
    assert action["undone"] is False
    assert action["section"] == "materialMovement"
    assert action["project"] == RAW_MATERIALS_PROJECT_KEY

    state = await store.get_cell_state(f"rm_material_movement/{row_id}/balanceOnSiteTons")
    assert state is not None
    assert state["revision"] == 1
    assert state["lastActionId"] == str(action["id"])


async def test_undo_redo_roundtrip(client) -> None:
    row_id = await _movement_row_id(client)
    before = await _movement_value(client, row_id, "balanceOnSiteTons")
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5

    undone = await _post_undo(client)
    assert undone["applied"] is True
    assert undone["reason"] == "applied"
    assert undone["rowId"] == row_id
    assert undone["field"] == "balanceOnSiteTons"
    assert undone["value"] == before
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == before

    redone = await _post_redo(client)
    assert redone["applied"] is True
    assert redone["value"] == 12.5
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5


async def test_undo_empty_stack(client, db_session) -> None:
    await _make_project_user(db_session, email="empty@example.local", name="Empty User", sub="empty-sub")
    response = await client.post(
        "/api/raw-materials/undo",
        headers=signed_identity_headers(email="empty@example.local", preferred_username="empty", subject="empty-sub"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert body["reason"] == "empty"


async def test_edit_clears_redo_branch(client) -> None:
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)
    await _post_undo(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 91.0)

    response = await _post_redo(client)
    assert response["applied"] is False
    assert response["reason"] == "empty"


async def test_undo_conflicts_with_another_user_edit(client, db_session, store) -> None:
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    await _make_project_user(db_session, email="viewer@example.local", name="Viewer User", sub="viewer-sub")

    response = await client.patch(
        "/api/raw-materials/movement",
        headers=signed_identity_headers(
            email="viewer@example.local", preferred_username="viewer", subject="viewer-sub"
        ),
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": row_id, "field": "balanceOnSiteTons", "value": 60.0}],
        },
    )
    assert response.status_code == 200

    undone = await _post_undo(client)
    assert undone["applied"] is False
    assert undone["reason"] == "conflict"
    assert undone["conflicts"][0]["field"] == "balanceOnSiteTons"
    assert undone["conflicts"][0]["user"] == "Viewer User"


async def test_undo_skip_conflicted_action(client, db_session) -> None:
    """Второе нажатие undo (skipActionId) пропускает конфликтное действие."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "consumptionFactTons", 81.0)

    await _make_project_user(db_session, email="second@example.local", name="Second User", sub="second-sub")
    second_headers = signed_identity_headers(
        email="second@example.local", preferred_username="second", subject="second-sub"
    )
    await client.patch(
        "/api/raw-materials/movement",
        headers=second_headers,
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": row_id, "field": "balanceOnSiteTons", "value": 13.5}],
        },
    )
    await _patch_movement(client, row_id, "balanceOnSiteTons", 14.5)
    await client.patch(
        "/api/raw-materials/movement",
        headers=second_headers,
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": row_id, "field": "balanceOnSiteTons", "value": 15.5}],
        },
    )

    conflicted = await _post_undo(client)
    assert conflicted["applied"] is False
    assert conflicted["reason"] == "conflict"
    conflicted_action_id = conflicted["conflicts"][0]["actionId"]

    skipped = await _post_undo(client, skip_action_id=conflicted_action_id)
    assert skipped["applied"] is True
    assert skipped["reason"] == "applied"
    assert skipped["field"] == "consumptionFactTons"
    assert skipped["value"] == 80.0
    assert await _movement_value(client, row_id, "consumptionFactTons") == 80.0


async def test_undo_locked_by_lease(client, store) -> None:
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    key = lease_key(f"rm_material_movement/{row_id}/balanceOnSiteTons", project=RAW_MATERIALS_PROJECT_KEY)
    await store.acquire_lease(key, {"userId": "other-user", "name": "Другой", "email": "", "sessionId": "s1"})

    undone = await _post_undo(client)
    assert undone["applied"] is False
    assert undone["reason"] == "locked"
    assert undone["conflicts"][0]["user"] == "Другой"


async def test_lease_acquire_renew_release(client, store) -> None:
    key = "rm_material_movement/00000000-0000-0000-0000-000000000001/consumptionFactTons"
    owner = {"userId": "u1", "name": "Один", "email": "", "sessionId": "s1"}
    other = {"userId": "u2", "name": "Два", "email": "", "sessionId": "s2"}

    assert await store.acquire_lease(key, owner) is None
    current = await store.acquire_lease(key, other)
    assert current is not None
    assert current["userId"] == "u1"

    assert await store.renew_lease(key, other) is False
    assert await store.renew_lease(key, owner) is True
    lease = await store.get_lease(key)
    assert lease is not None
    assert lease["userId"] == "u1"

    assert await store.release_lease(key, other) is False
    assert await store.release_lease(key, owner) is True
    assert await store.get_lease(key) is None


async def test_publish_and_subscribe(client, store) -> None:
    row_id = await _movement_row_id(client)
    received: list[dict] = []

    async def collect() -> None:
        async for message in store.subscribe("rm:raw_materials:materialMovement:2026-08-01"):
            received.append(message)
            if message["type"] == "cell:changed":
                return

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.05)
    collector.cancel()
    assert received, "cell:changed не пришёл в pub/sub"
    event = received[0]
    assert event["type"] == "cell:changed"
    assert event["user"]["userId"]
    assert event["rowId"] == row_id
    assert event["field"] == "balanceOnSiteTons"
    assert event["value"] == 12.5


async def test_lease_keys_scoped_by_project(store) -> None:
    """Одна и та же ячейка в разных проектах арендуется независимо."""
    owner_a = {"userId": "u1", "name": "А", "email": "", "sessionId": "s1"}
    owner_b = {"userId": "u2", "name": "Б", "email": "", "sessionId": "s2"}
    cell = "rm_material_movement/00000000-0000-0000-0000-000000000001/consumptionFactTons"

    key_a = lease_key(cell, project="raw_materials")
    key_b = lease_key(cell, project="kppk")

    assert await store.acquire_lease(key_a, owner_a) is None
    assert await store.acquire_lease(key_b, owner_b) is None
    assert (await store.get_lease(key_a))["userId"] == "u1"
    assert (await store.get_lease(key_b))["userId"] == "u2"
    assert await store.release_lease(key_a, owner_a) is True
    assert await store.get_lease(key_b) is not None


async def _make_project(db_session, *, key: str, name: str) -> None:
    """Создаёт проект, если его ещё нет."""
    existing = (await db_session.exec(select(Project).where(col(Project.key) == key))).one_or_none()
    if existing is None:
        db_session.add(Project(key=key, name=name))
        await db_session.commit()


async def _grant_admin(db_session, *, email: str, project_key: str) -> None:
    admin = (await db_session.exec(select(User).where(col(User.email) == email))).one()
    await grant_project_role(db_session, admin.id, project_key, ProjectRole.ADMIN)
    await activate_project_user(db_session, admin.id, project_key)


class FakeWebSocket:
    """Двойник WebSocket: прогоняет raw_materials_ws без сетевого слоя."""

    def __init__(self, *, project: str, origin: str | None = None) -> None:
        self.query_params = {
            "project": project,
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
        }
        headers = signed_identity_headers(**admin_headers())
        if origin is not None:
            headers["Origin"] = origin
        self.headers = Headers(headers)
        self.incoming: list[dict] = []
        self.disconnect = False
        self.sent: list[dict] = []
        self.closed_code: int | None = None
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def receive_json(self) -> dict:
        while True:
            if self.incoming:
                return self.incoming.pop(0)
            if self.disconnect:
                raise WebSocketDisconnect
            await asyncio.sleep(0.005)


async def _run_collab_ws(db_session, fake: FakeWebSocket) -> asyncio.Task:
    task = asyncio.create_task(raw_materials_ws(cast("WebSocket", fake), db_session))
    await asyncio.sleep(0.02)
    return task


async def _wait_until(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    pytest.fail("Не дождались условия WebSocket-теста")


async def test_ws_project_isolation(client, db_session, store) -> None:
    """Арена и присутствие проекта не пересекаются с другими проектами."""
    await _make_project(db_session, key="kppk", name="КППК")
    await _grant_admin(db_session, email="admin@example.local", project_key="kppk")

    cell = "rm_material_movement/00000000-0000-0000-0000-000000000002/consumptionFactTons"
    await store.acquire_lease(
        lease_key(cell, project="raw_materials"), {"userId": "u-other", "name": "Др", "email": ""}
    )

    fake = FakeWebSocket(project="kppk")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    hello = next(message for message in fake.sent if message["type"] == "hello")
    assert hello["room"].startswith("rm:kppk:")

    fake.incoming.append({"type": "lease:acquire", "cells": [cell]})
    await _wait_until(lambda: any(message.get("type") == "lease:granted" for message in fake.sent))
    granted = next(message for message in fake.sent if message["type"] == "lease:granted")
    assert cell in granted["cells"]

    members_kppk = await store.room_members("rm:kppk:materialMovement:2026-08-01")
    members_rm = await store.room_members("rm:raw_materials:materialMovement:2026-08-01")
    assert len(members_kppk) == 1
    assert len(members_rm) == 0

    fake.disconnect = True
    await task


async def test_ws_ping_refreshes_presence(db_session, store) -> None:
    """Heartbeat продлевает присутствие: участник не исчезает из комнаты."""
    room = "rm:raw_materials:materialMovement:2026-08-01"
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    hello = next(message for message in fake.sent if message["type"] == "hello")
    user_id = hello["user"]["userId"]
    before = await store.presence_expires_at(room, user_id)
    assert before is not None

    fake.incoming.append({"type": "ping"})
    await _wait_until(lambda: any(message.get("type") == "pong" for message in fake.sent))
    after = await store.presence_expires_at(room, user_id)
    assert after is not None
    assert after > before

    fake.disconnect = True
    await task


async def test_ws_rejects_unexpected_origin(db_session, store) -> None:
    """Origin вне BACKEND_CORS_ORIGINS отклоняется на хендшейке."""
    fake = FakeWebSocket(project="raw_materials", origin="https://evil.example")
    await raw_materials_ws(cast("WebSocket", fake), db_session)
    assert fake.closed_code == 4403
    assert fake.accepted is False
    assert not await store.room_members("rm:raw_materials:materialMovement:2026-08-01")


async def test_ws_connect_clears_undo_stack(client, db_session, store) -> None:
    """Перезагрузка страницы (новое WS-подключение) обнуляет undo/redo."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    undone = await _post_undo(client)
    assert undone["applied"] is False
    assert undone["reason"] == "empty"

    fake.disconnect = True
    await task
