"""Undo/redo of plan row lifecycle changes in the «Ремонт» plan.

Adding a row journals «__created», deleting it journals «__deleted», and the
asset assignment of a draft row journals the «asset_id» cell. Undo restores
the previous row set; redo applies the change again.
"""

from conftest import signed_identity_headers
from httpx2 import AsyncClient

from src.renovation.plan import RenovationPlanRow

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
SESSION = "plan-row-session"


async def _rows(client: AsyncClient) -> list[dict]:
    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["rows"]


async def _undo(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={"section": "renovationPlan"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _redo(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/renovation/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={"section": "renovationPlan"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_added_row_is_removed_by_undo_and_restored_by_redo(client: AsyncClient, db_session) -> None:
    rows_before = await _rows(client)
    response = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={},
    )
    assert response.status_code == 200, response.text
    row_id = response.json()["id"]
    assert any(row["id"] == row_id for row in await _rows(client))

    undone = await _undo(client)
    assert undone["applied"] is True
    assert not any(row["id"] == row_id for row in await _rows(client))
    assert await db_session.get(RenovationPlanRow, row_id) is None

    redone = await _redo(client)
    assert redone["applied"] is True
    restored = await db_session.get(RenovationPlanRow, row_id)
    assert restored is not None
    assert len(await _rows(client)) == len(rows_before) + 1


async def test_deleted_empty_row_is_restored_by_undo(client: AsyncClient, db_session) -> None:
    created = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={},
    )
    assert created.status_code == 200, created.text
    row_id = created.json()["id"]
    position = created.json().get("position")

    response = await client.delete(
        f"/api/renovation/plan/rows/{row_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
    )
    assert response.status_code == 204, response.text
    assert await db_session.get(RenovationPlanRow, row_id) is None

    undone = await _undo(client)
    assert undone["applied"] is True
    restored = await db_session.get(RenovationPlanRow, row_id)
    assert restored is not None
    if position is not None:
        assert restored.position == position


async def test_asset_assignment_is_undoable(client: AsyncClient, db_session) -> None:
    meta = await client.get("/api/renovation/meta", headers=ADMIN_HEADERS)
    assert meta.status_code == 200, meta.text
    assets = meta.json()["assets"]
    if not assets:
        return
    asset_id = assets[0]["id"]
    created = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={},
    )
    assert created.status_code == 200, created.text
    row_id = created.json()["id"]

    assigned = await client.patch(
        f"/api/renovation/plan/rows/{row_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={"assetId": asset_id},
    )
    assert assigned.status_code == 200, assigned.text

    # Undo the assignment: the row loses its asset again.
    assert (await _undo(client))["applied"] is True
    row = await db_session.get(RenovationPlanRow, row_id)
    assert row is not None
    assert row.asset_id is None
