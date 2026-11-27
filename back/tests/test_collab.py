"""Collaboration: edit journal, undo/redo, conflicts and cell leases."""

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
    IDENTITY_TICKET_MAX_AGE,
    InvalidIdentityError,
    activate_project_user,
    grant_project_assets,
    grant_project_role,
    verify_identity_ticket,
    verify_signed_identity,
)
from src.collab.store import lease_key
from src.collab.ws import raw_materials_ws
from src.raw_materials.models import MaterialMovement

# Undo/redo history is scoped by browser session (the page-lifetime id the
# frontend sends with every edit): tests use one fixed session by default.
TEST_SESSION_ID = "test-session"


async def _patch_movement(client, row_id: str, field: str, value: float) -> dict:
    response = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
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


async def _post_undo(
    client,
    *,
    skip_action_id: int | None = None,
    section: str = "materialMovement",
    report_month: str | None = "2026-08-01",
    session_id: str = TEST_SESSION_ID,
) -> dict:
    response = await client.post(
        "/api/raw-materials/undo",
        params={"session_id": session_id},
        headers=signed_identity_headers(**admin_headers()),
        json={"skipActionId": skip_action_id, "section": section, "reportMonth": report_month},
    )
    assert response.status_code == 200
    return response.json()


async def _post_redo(
    client,
    *,
    section: str = "materialMovement",
    report_month: str | None = "2026-08-01",
    session_id: str = TEST_SESSION_ID,
) -> dict:
    response = await client.post(
        "/api/raw-materials/redo",
        params={"session_id": session_id},
        headers=signed_identity_headers(**admin_headers()),
        json={"section": section, "reportMonth": report_month},
    )
    assert response.status_code == 200
    return response.json()


def _identity_ticket(headers: dict[str, str]) -> str:
    """WebSocket ticket in the frontend format (route handler /api/collab/ticket)."""
    ticket = {
        "h": {
            name: headers.get(name, "")
            for name in (
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
    assert identity.email == "admin@example.ru"
    assert identity.oidc_sub == "admin-sub"


async def test_ws_ticket_rejects_forged_signature() -> None:
    headers = signed_identity_headers(**admin_headers())
    headers["X-ReportCollector-Identity-Signature"] = "0" * 64
    with pytest.raises(InvalidIdentityError):
        verify_identity_ticket(_identity_ticket(headers))


async def test_ws_ticket_rejects_stale_timestamp() -> None:
    stale = int(datetime.now(UTC).timestamp()) - 300
    headers = signed_identity_headers(**admin_headers(), timestamp=stale)
    with pytest.raises(InvalidIdentityError):
        verify_identity_ticket(_identity_ticket(headers))


async def test_ws_ticket_expires_before_plain_identity_headers() -> None:
    """AUD-20: the ticket window stays short even when AUTH_IDENTITY_TTL is wide."""
    past_ticket_window = int(datetime.now(UTC).timestamp()) - (IDENTITY_TICKET_MAX_AGE + 5)
    headers = signed_identity_headers(**admin_headers(), timestamp=past_ticket_window)
    assert verify_signed_identity(headers).email == "admin@example.ru"
    with pytest.raises(InvalidIdentityError):
        verify_identity_ticket(_identity_ticket(headers))


@pytest.fixture(autouse=True)
async def restore_movement_seed(db_session) -> AsyncGenerator[None]:
    """Restores the August «Движение» rows to the seed state (don't break other tests)."""
    yield
    rows = (
        await db_session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == date(2026, 8, 1)))
    ).all()
    for row in rows:
        row.balance_on_site_tons = 5.0
        row.updater_user_id = None
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
    """Create a «Сырьё» project user with the asset_worker role (for foreign-user tests)."""
    now = datetime.now(UTC)
    user = User(email=email, name=name, created_at=now, updated_at=now)
    db_session.add(user)
    await db_session.flush()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ"])
    await db_session.commit()
    return user


async def test_edit_writes_journal_and_cell_state(client, db_session, store) -> None:
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    admin = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    actions = await store.list_actions(str(admin.id), TEST_SESSION_ID, "materialMovement/2026-08-01")
    assert len(actions) == 1
    action = actions[0]
    assert len(action["cells"]) == 1
    cell = action["cells"][0]
    assert cell["targetTable"] == "rm_material_movements"
    assert cell["rowId"] == row_id
    assert cell["field"] == "balanceOnSiteTons"
    assert cell["oldValue"] == 5.0
    assert cell["newValue"] == 12.5
    assert action["undone"] is False
    assert action["section"] == "materialMovement"
    assert action["project"] == RAW_MATERIALS_PROJECT_KEY

    state = await store.get_cell_state(f"rm_material_movements/{row_id}/balanceOnSiteTons")
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


async def test_undo_batch_restores_all_cells(client, db_session, store) -> None:
    """One PATCH of several cells is one action: undo restores the whole batch."""
    row_id = await _movement_row_id(client)
    response = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={
            "reportMonth": "2026-08-01",
            "updates": [
                {"rowId": row_id, "field": "balanceOnSiteTons", "value": 12.5},
                {"rowId": row_id, "field": "consumptionFactTons", "value": 81.0},
            ],
        },
    )
    assert response.status_code == 200
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5
    assert await _movement_value(client, row_id, "consumptionFactTons") == 81.0

    admin = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    actions = await store.list_actions(str(admin.id), TEST_SESSION_ID, "materialMovement/2026-08-01")
    assert len(actions) == 1
    assert len(actions[0]["cells"]) == 2

    undone = await _post_undo(client)
    assert undone["applied"] is True
    assert undone["reason"] == "applied"
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 5.0
    assert await _movement_value(client, row_id, "consumptionFactTons") == 80.0

    redone = await _post_redo(client)
    assert redone["applied"] is True
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5
    assert await _movement_value(client, row_id, "consumptionFactTons") == 81.0


async def test_undo_twice_same_cell_by_same_user(client) -> None:
    """Consecutive edits of one cell by the same user undo cleanly (no false conflict)."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 91.0)
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 91.0

    first = await _post_undo(client)
    assert first["applied"] is True
    assert first["value"] == 12.5
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5

    second = await _post_undo(client)
    assert second["applied"] is True
    assert second["value"] == 5.0
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 5.0

    # Redo replays undone actions in their original order (the reverse of
    # the undo sequence): the first edit first, then the second.
    redone = await _post_redo(client)
    assert redone["applied"] is True
    assert redone["value"] == 12.5
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 12.5

    redone_again = await _post_redo(client)
    assert redone_again["applied"] is True
    assert redone_again["value"] == 91.0
    assert await _movement_value(client, row_id, "balanceOnSiteTons") == 91.0


async def test_undo_history_is_per_form(client) -> None:
    """Undo/redo history is per form (section + month)."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    from_assets = await _post_undo(client, section="assets", report_month=None)
    assert from_assets["applied"] is False
    assert from_assets["reason"] == "empty"

    on_movement = await _post_undo(client)
    assert on_movement["applied"] is True
    assert on_movement["field"] == "balanceOnSiteTons"
    assert on_movement["value"] == 5.0

    # Another «Движение» month doesn't share history with August either.
    september = await _post_undo(client, section="materialMovement", report_month="2026-09-01")
    assert september["applied"] is False
    assert september["reason"] == "empty"


async def test_undo_empty_stack(client, db_session) -> None:
    await _make_project_user(db_session, email="empty@example.ru", name="Empty User", sub="empty-sub")
    headers = signed_identity_headers(email="empty@example.ru", preferred_username="empty", subject="empty-sub")
    response = await client.post("/api/raw-materials/undo", params={"session_id": TEST_SESSION_ID}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert body["reason"] == "empty"

    state = await client.get("/api/raw-materials/undo-state", params={"session_id": TEST_SESSION_ID}, headers=headers)
    assert state.status_code == 200
    assert state.json() == {"canUndo": False, "canRedo": False}


async def test_undo_state_endpoint(client) -> None:
    """undo-state reflects the presence of actions in the user's stack."""
    headers = signed_identity_headers(**admin_headers())
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    params = {"section": "materialMovement", "report_month": "2026-08-01", "session_id": TEST_SESSION_ID}
    state = (await client.get("/api/raw-materials/undo-state", params=params, headers=headers)).json()
    assert state == {"canUndo": True, "canRedo": False}

    await _post_undo(client)
    state = (await client.get("/api/raw-materials/undo-state", params=params, headers=headers)).json()
    assert state == {"canUndo": False, "canRedo": True}


async def test_undo_history_is_session_scoped(client) -> None:
    """A page reload (a new session id) starts with an empty undo history."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    # The previous page session can't see or undo this edit.
    other = await _post_undo(client, session_id="previous-page-session")
    assert other["applied"] is False
    assert other["reason"] == "empty"

    # The current session can.
    undone = await _post_undo(client)
    assert undone["applied"] is True
    assert undone["field"] == "balanceOnSiteTons"
    assert undone["value"] == 5.0


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

    await _make_project_user(db_session, email="viewer@example.ru", name="Viewer User", sub="viewer-sub")

    response = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(email="viewer@example.ru", preferred_username="viewer", subject="viewer-sub"),
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
    """A second undo press (skipActionId) skips the conflicting action."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "consumptionFactTons", 81.0)

    await _make_project_user(db_session, email="second@example.ru", name="Second User", sub="second-sub")
    second_headers = signed_identity_headers(
        email="second@example.ru", preferred_username="second", subject="second-sub"
    )
    await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=second_headers,
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": row_id, "field": "balanceOnSiteTons", "value": 13.5}],
        },
    )
    await _patch_movement(client, row_id, "balanceOnSiteTons", 14.5)
    await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
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

    key = lease_key(f"rm_material_movements/{row_id}/balanceOnSiteTons", project=RAW_MATERIALS_PROJECT_KEY)
    await store.acquire_lease(key, {"userId": "other-user", "name": "Другой", "email": "", "sessionId": "s1"})

    undone = await _post_undo(client)
    assert undone["applied"] is False
    assert undone["reason"] == "locked"
    assert undone["conflicts"][0]["user"] == "Другой"


async def test_lease_acquire_renew_release(client, store) -> None:
    key = "rm_material_movements/00000000-0000-0000-0000-000000000001/consumptionFactTons"
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
    """The same cell is leased independently in different projects."""
    owner_a = {"userId": "u1", "name": "А", "email": "", "sessionId": "s1"}
    owner_b = {"userId": "u2", "name": "Б", "email": "", "sessionId": "s2"}
    cell = "rm_material_movements/00000000-0000-0000-0000-000000000001/consumptionFactTons"

    key_a = lease_key(cell, project="raw_materials")
    key_b = lease_key(cell, project="kppk")

    assert await store.acquire_lease(key_a, owner_a) is None
    assert await store.acquire_lease(key_b, owner_b) is None
    assert (await store.get_lease(key_a))["userId"] == "u1"
    assert (await store.get_lease(key_b))["userId"] == "u2"
    assert await store.release_lease(key_a, owner_a) is True
    assert await store.get_lease(key_b) is not None


async def _make_project(db_session, *, key: str, name: str) -> None:
    """Create the project if it doesn't exist yet."""
    existing = (await db_session.exec(select(Project).where(col(Project.key) == key))).one_or_none()
    if existing is None:
        db_session.add(Project(key=key, name=name))
        await db_session.commit()


async def _grant_admin(db_session, *, email: str, project_key: str) -> None:
    admin = (await db_session.exec(select(User).where(col(User.email) == email))).one()
    await grant_project_role(db_session, admin.id, project_key, ProjectRole.ADMIN)
    await activate_project_user(db_session, admin.id, project_key)


class FakeWebSocket:
    """WebSocket double: runs raw_materials_ws without the network layer."""

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

    async def receive_text(self) -> str:
        return json.dumps(await self.receive_json())

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": json.dumps(await self.receive_json())}


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


async def test_undo_redo_dictionary_raw_material(client, store) -> None:
    """A raw-material item name edit is undone and redone (dictionary undo)."""
    rows = (
        await client.get("/api/raw-materials/raw-materials", headers=signed_identity_headers(**admin_headers()))
    ).json()
    target = next(row for row in rows if row["assetNames"])
    original = target["name"]
    temporary = f"{original} (временное)"

    response = await client.patch(
        f"/api/raw-materials/raw-materials/{target['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": temporary},
    )
    assert response.status_code == 200
    assert response.json()["name"] == temporary

    undone = await _post_undo(client, section="rawMaterials", report_month=None)
    assert undone["applied"] is True
    assert undone["field"] == "name"
    assert undone["value"] == original
    rows = (
        await client.get("/api/raw-materials/raw-materials", headers=signed_identity_headers(**admin_headers()))
    ).json()
    assert next(row for row in rows if row["id"] == target["id"])["name"] == original

    redone = await _post_redo(client, section="rawMaterials", report_month=None)
    assert redone["applied"] is True
    assert redone["value"] == temporary

    # Restore the original name so other tests aren't affected.
    await client.patch(
        f"/api/raw-materials/raw-materials/{target['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": original},
    )


async def test_undo_redo_dictionary_asset(client, store) -> None:
    """An asset name edit is undone and redone."""
    rows = (await client.get("/api/raw-materials/assets", headers=signed_identity_headers(**admin_headers()))).json()
    target = rows[0]
    original = target["name"]
    temporary = f"{original} (временный)"

    response = await client.patch(
        f"/api/raw-materials/assets/{target['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": temporary},
    )
    assert response.status_code == 200

    undone = await _post_undo(client, section="assets", report_month=None)
    assert undone["applied"] is True
    assert undone["field"] == "name"
    assert undone["value"] == original

    # Restore the original name.
    await client.patch(
        f"/api/raw-materials/assets/{target['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": original},
    )


@pytest.mark.real_schema
async def test_ws_project_isolation(client, db_session, store) -> None:
    """A project's leases and presence don't intersect other projects."""
    await _make_project(db_session, key="kppk", name="КППК")
    await _grant_admin(db_session, email="admin@example.ru", project_key="kppk")

    cell = "rm_material_movements/00000000-0000-0000-0000-000000000002/consumptionFactTons"
    await store.acquire_lease(
        lease_key(cell, project="raw_materials"), {"userId": "u-other", "name": "Др", "email": ""}
    )

    fake = FakeWebSocket(project="kppk")
    task = await _run_collab_ws(db_session, fake)
    await task
    # A raw-materials resolver cannot authorize a client-selected project,
    # even if the account separately has a role there.
    assert fake.closed_code == 4403
    assert not fake.accepted
    assert await store.room_members("rm:kppk:materialMovement:2026-08-01") == {}


@pytest.mark.real_schema
async def test_ws_oversized_lease_selection_is_an_error(db_session) -> None:
    """A selection beyond the lease cap is rejected explicitly, never truncated."""
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))
    fake.sent.clear()
    fake.incoming.append(
        {
            "type": "lease:acquire",
            "cells": [f"rm_material_movements/row-{index}/balanceOnSiteTons" for index in range(501)],
        }
    )
    await _wait_until(lambda: any(message.get("type") == "error" for message in fake.sent))
    error = next(message for message in fake.sent if message["type"] == "error")
    assert "Слишком много ячеек" in error["message"]
    fake.disconnect = True
    await task


@pytest.mark.real_schema
async def test_ws_batched_lease_acquire_and_release(client, db_session) -> None:
    """One WS message leases/releases the whole cell set with per-cell events."""
    row_id = await _movement_row_id(client)
    cells = [f"rm_material_movements/{row_id}/{field}" for field in ("balanceOnSiteTons", "consumptionFactTons")]
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    try:
        await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))
        fake.sent.clear()
        fake.incoming.append({"type": "lease:acquire", "cells": cells})
        await _wait_until(
            lambda: any(
                message.get("type") == "lease:granted" and message.get("cells") == cells for message in fake.sent
            )
        )
        assert not any(message.get("type") == "lease:denied" for message in fake.sent)
        await _wait_until(lambda: sum(message.get("type") == "cell:locked" for message in fake.sent) == len(cells))
        locked = [message for message in fake.sent if message.get("type") == "cell:locked"]
        assert {message["key"] for message in locked} == set(cells)

        fake.sent.clear()
        fake.incoming.append({"type": "lease:release", "cells": cells})
        await _wait_until(lambda: sum(message.get("type") == "cell:unlocked" for message in fake.sent) == len(cells))
        unlocked = [message for message in fake.sent if message.get("type") == "cell:unlocked"]
        assert {message["key"] for message in unlocked} == set(cells)
    finally:
        fake.disconnect = True
        await task


@pytest.mark.real_schema
async def test_ws_ping_refreshes_presence(db_session, store, presence_expiry) -> None:
    """Heartbeat renews presence: the member doesn't vanish from the room."""
    room = "rm:raw_materials:materialMovement:2026-08-01"
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    hello = next(message for message in fake.sent if message["type"] == "hello")
    user_id = hello["user"]["userId"]
    before = await presence_expiry(store, room, user_id)
    assert before is not None

    fake.incoming.append({"type": "ping"})
    await _wait_until(lambda: any(message.get("type") == "pong" for message in fake.sent))
    after = await presence_expiry(store, room, user_id)
    assert after is not None
    assert after > before

    fake.disconnect = True
    await task


@pytest.mark.real_schema
async def test_ws_ping_readds_expired_presence(db_session, store) -> None:
    """A returning member shows up in «На форме» on the very first heartbeat."""
    room = "rm:raw_materials:materialMovement:2026-08-01"
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    hello = next(message for message in fake.sent if message["type"] == "hello")
    user_id = hello["user"]["userId"]

    # The member left: presence expired and was cleaned up.
    await store.room_leave(room, user_id)
    assert not await store.room_members(room)

    fake.sent.clear()
    fake.incoming.append({"type": "ping"})
    await _wait_until(lambda: any(message.get("type") == "pong" for message in fake.sent))

    assert user_id in await store.room_members(room)

    fake.disconnect = True
    await task


async def test_refresh_presence_reports_missing_member(store) -> None:
    """refresh_presence returns False when the member isn't in the room (expired)."""
    room = "rm:test:materialMovement:2026-08-01"
    assert await store.refresh_presence(room, "u-1") is False
    await store.room_join(room, "u-1", {"name": "А"})
    assert await store.refresh_presence(room, "u-1") is True


@pytest.mark.real_schema
async def test_ws_rejects_unexpected_origin(db_session, store) -> None:
    """An Origin outside BACKEND_CORS_ORIGINS is rejected on the handshake."""
    fake = FakeWebSocket(project="raw_materials", origin="https://evil.example")
    await raw_materials_ws(cast("WebSocket", fake), db_session)
    assert fake.closed_code == 4403
    assert fake.accepted is False
    assert not await store.room_members("rm:raw_materials:materialMovement:2026-08-01")


@pytest.mark.real_schema
async def test_ws_connect_keeps_undo_stack(client, db_session, store) -> None:
    """Undo/redo history survives reconnects (form switches, reload)."""
    row_id = await _movement_row_id(client)
    await _patch_movement(client, row_id, "balanceOnSiteTons", 12.5)

    # A new WS connection (other form/reload) doesn't clear the stack.
    fake = FakeWebSocket(project="raw_materials")
    task = await _run_collab_ws(db_session, fake)
    await _wait_until(lambda: any(message.get("type") == "hello" for message in fake.sent))

    undone = await _post_undo(client)
    assert undone["applied"] is True

    fake.disconnect = True
    await task


async def test_created_asset_is_undoable_and_redoable(client) -> None:
    """A created dictionary record is undone (record gone) and redone (record back)."""
    created = await client.post(
        "/api/raw-materials/assets",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Тестовый актив на отмену"},
    )
    assert created.status_code == 201
    created_id = created.json()["id"]

    undone = await _post_undo(client, section="assets", report_month=None)
    assert undone["applied"] is True
    assert undone["field"] == "__created"

    rows = (await client.get("/api/raw-materials/assets", headers=signed_identity_headers(**admin_headers()))).json()
    assert all(row["id"] != created_id for row in rows)

    redone = await _post_redo(client, section="assets", report_month=None)
    assert redone["applied"] is True

    rows = (await client.get("/api/raw-materials/assets", headers=signed_identity_headers(**admin_headers()))).json()
    restored = next(row for row in rows if row["id"] == created_id)
    assert restored["name"] == "Тестовый актив на отмену"


async def test_created_user_undo_deactivates_redo_restores(client, db_session) -> None:
    """Users are never deleted: undo of a user-create deactivates the project link, redo restores it."""
    created = await client.post(
        "/api/raw-materials/users",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={
            "email": "accidental@example.ru",
            "fullname": "Случайный Случай",
            "isAdmin": False,
            "assetNames": ["СНХЗ"],
        },
    )
    assert created.status_code == 201
    created_id = created.json()["id"]

    undone = await _post_undo(client, section="users", report_month=None)
    assert undone["applied"] is True
    assert undone["field"] == "__created"

    rows = (await client.get("/api/raw-materials/users", headers=signed_identity_headers(**admin_headers()))).json()
    row = next(candidate for candidate in rows if candidate["id"] == created_id)
    assert row["isActive"] is False
    assert row["assetNames"] == []

    redone = await _post_redo(client, section="users", report_month=None)
    assert redone["applied"] is True

    rows = (await client.get("/api/raw-materials/users", headers=signed_identity_headers(**admin_headers()))).json()
    row = next(candidate for candidate in rows if candidate["id"] == created_id)
    assert row["isActive"] is True
    assert row["assetNames"] == ["СНХЗ"]


async def test_created_basis_undo_after_manual_delete_is_noop(client) -> None:
    """Create -> delete manually -> undo: the stale __created action is a no-op, not an error."""
    created = await client.post(
        "/api/raw-materials/delivery-bases",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Базис под ручное удаление"},
    )
    assert created.status_code == 201
    created_id = created.json()["id"]

    deleted = await client.delete(
        f"/api/raw-materials/delivery-bases/{created_id}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 204

    undone = await _post_undo(client, section="deliveryBases", report_month=None)
    assert undone["applied"] is True
    assert undone["field"] == "__created"

    rows = (
        await client.get("/api/raw-materials/delivery-bases", headers=signed_identity_headers(**admin_headers()))
    ).json()
    assert all(row["id"] != created_id for row in rows)


async def test_created_basis_undo_conflicts_when_used_then_skips(client) -> None:
    """Undoing a create whose record is already referenced is a skippable conflict, not a 500."""
    created = await client.post(
        "/api/raw-materials/delivery-bases",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Используемый базис"},
    )
    assert created.status_code == 201
    created_id = created.json()["id"]
    created_name = created.json()["name"]

    prices = (await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**admin_headers()))).json()
    await client.patch(
        "/api/raw-materials/prices",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"updates": [{"rowId": prices[0]["id"], "field": "deliveryBasisName", "value": created_name}]},
    )

    undone = await _post_undo(client, section="deliveryBases", report_month=None)
    assert undone["applied"] is False
    assert undone["reason"] == "conflict"
    assert undone["conflicts"][0]["field"] == "__created"
    conflict_action_id = undone["conflicts"][0]["actionId"]

    skipped = await _post_undo(client, section="deliveryBases", report_month=None, skip_action_id=conflict_action_id)
    assert skipped["applied"] is False
    assert skipped["reason"] == "empty"

    # The used basis is still there.
    rows = (
        await client.get("/api/raw-materials/delivery-bases", headers=signed_identity_headers(**admin_headers()))
    ).json()
    assert any(row["id"] == created_id for row in rows)
