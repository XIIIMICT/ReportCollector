"""Tests for the «Ремонт» editable dictionaries: assets, catalog options, users, export."""

from __future__ import annotations

from io import BytesIO
from uuid import UUID

from conftest import signed_identity_headers
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project
from src.renovation.constants import R_ASSETS, RENOVATION_PROJECT_KEY
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")
TEST_SESSION_ID = "dict-test-session"


async def _asset_by_code(session: AsyncSession, code: str) -> RAsset:
    return (await session.exec(select(RAsset).where(col(RAsset.code) == code))).one()


async def _project_id(session: AsyncSession) -> UUID:
    return (await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))).one()


# ─── Assets ────────────────────────────────────────────────────────────────


async def test_dictionary_endpoints_require_admin(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/dictionary/assets", headers=VIEWER_HEADERS)
    assert response.status_code == 403
    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=VIEWER_HEADERS,
        json={"name": "X", "repairCode": "X1"},
    )
    assert response.status_code == 403
    response = await client.get("/api/renovation/dictionary/users", headers=VIEWER_HEADERS)
    assert response.status_code == 403


async def test_assets_create_read_patch_delete(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    seeded = response.json()
    assert len(seeded) >= 2

    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=ADMIN_HEADERS,
        json={"name": "Новый цех", "repairCode": "NEW-1"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Новый цех"
    assert created["repairCode"] == "NEW-1"
    assert created["createdAt"] is not None
    assert created["updatedBy"] == "Admin User"

    response = await client.patch(
        f"/api/renovation/dictionary/assets/{created['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"name": "Переименованный цех", "repairCode": "NEW-2"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Переименованный цех"
    assert updated["repairCode"] == "NEW-2"

    response = await client.delete(
        f"/api/renovation/dictionary/assets/{created['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 204


async def test_asset_delete_blocked_while_used(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    asset = await _asset_by_code(db_session, "bsk")
    response = await client.delete(
        f"/api/renovation/dictionary/assets/{asset.id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 409


async def test_asset_undo_restores_previous_name(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=ADMIN_HEADERS,
        json={"name": "Цех A", "repairCode": "A1"},
    )
    asset_id = response.json()["id"]
    await client.patch(
        f"/api/renovation/dictionary/assets/{asset_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"name": "Цех B", "repairCode": "A1"},
    )
    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationAssets"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    by_id = {row["id"]: row for row in rows.json()}
    assert by_id[asset_id]["name"] == "Цех A"


async def test_asset_batch_update_is_one_undo_action(client: AsyncClient) -> None:
    """One batch request edits several rows and undoes as a single action."""
    ids = []
    for index in range(2):
        response = await client.post(
            "/api/renovation/dictionary/assets",
            headers=ADMIN_HEADERS,
            json={"name": f"Партия {index}", "repairCode": f"P{index}"},
        )
        ids.append(response.json()["id"])

    response = await client.patch(
        "/api/renovation/dictionary",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={
            "section": "assets",
            "updates": [
                {"rowId": ids[0], "field": "name", "value": "Партия A"},
                {"rowId": ids[1], "field": "name", "value": "Партия B"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["applied"] == 2

    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    by_id = {row["id"]: row for row in rows.json()}
    assert by_id[ids[0]]["name"] == "Партия A"
    assert by_id[ids[1]]["name"] == "Партия B"

    # A single undo reverts both rows.
    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationAssets"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    by_id = {row["id"]: row for row in rows.json()}
    assert by_id[ids[0]]["name"] == "Партия 0"
    assert by_id[ids[1]]["name"] == "Партия 1"


async def test_asset_delete_undo_restores_then_redo_deletes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=ADMIN_HEADERS,
        json={"name": "Цех C", "repairCode": "C1"},
    )
    assert response.status_code == 201
    asset_id = response.json()["id"]
    response = await client.delete(
        f"/api/renovation/dictionary/assets/{asset_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 204
    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    assert asset_id not in {row["id"] for row in rows.json()}

    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationAssets"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    restored = {row["id"]: row for row in rows.json()}.get(asset_id)
    assert restored is not None
    assert restored["name"] == "Цех C"
    assert restored["repairCode"] == "C1"

    response = await client.post(
        "/api/renovation/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationAssets"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    assert asset_id not in {row["id"] for row in rows.json()}


async def test_asset_duplicate_name_and_repair_code_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=ADMIN_HEADERS,
        json={"name": "АО «БСК»", "repairCode": "UNIQ-1"},
    )
    assert response.status_code == 409
    assert "названием" in response.json()["detail"]
    response = await client.post(
        "/api/renovation/dictionary/assets",
        headers=ADMIN_HEADERS,
        json={"name": "Уникальный цех", "repairCode": "BSKR"},
    )
    assert response.status_code == 409
    assert "ремонтным кодом" in response.json()["detail"]


# ─── Catalog options ───────────────────────────────────────────────────────


async def test_catalog_options_create_read_patch_delete(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.get("/api/renovation/dictionary/catalogs/criticality", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    seeded = response.json()
    assert len(seeded) == 4
    assert all(option["valueId"] for option in seeded)

    response = await client.post(
        "/api/renovation/dictionary/catalogs/criticality/options",
        headers=ADMIN_HEADERS,
        json={"label": "5"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["label"] == "5"

    response = await client.patch(
        f"/api/renovation/dictionary/catalogs/criticality/options/{created['valueId']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"label": "5 (критично)"},
    )
    assert response.status_code == 200
    assert response.json()["label"] == "5 (критично)"

    response = await client.delete(
        f"/api/renovation/dictionary/catalogs/criticality/options/{created['valueId']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 204


async def test_catalog_option_delete_undo_restores_then_redo_deletes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/catalogs/criticality/options",
        headers=ADMIN_HEADERS,
        json={"label": "7"},
    )
    assert response.status_code == 201
    option_id = response.json()["valueId"]
    response = await client.delete(
        f"/api/renovation/dictionary/catalogs/criticality/options/{option_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 204

    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationCatalog:criticality"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/catalogs/criticality", headers=ADMIN_HEADERS)
    restored = {row["valueId"]: row for row in rows.json()}.get(option_id)
    assert restored is not None
    assert restored["label"] == "7"

    response = await client.post(
        "/api/renovation/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationCatalog:criticality"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    rows = await client.get("/api/renovation/dictionary/catalogs/criticality", headers=ADMIN_HEADERS)
    assert option_id not in {row["valueId"] for row in rows.json()}


async def test_catalog_option_delete_blocked_while_used(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    option = (
        await db_session.exec(
            select(RenovationReferenceValue).where(
                col(RenovationReferenceValue.catalog) == "criticality",
                col(RenovationReferenceValue.value_key) == "criticality_001",
            )
        )
    ).one()
    row = (
        await db_session.exec(
            select(RenovationPlanRow)
            .join(RenovationPlan, col(RenovationPlanRow.plan_id) == RenovationPlan.id)
            .where(col(RenovationPlan.state) == RPlanState.WORKING)
        )
    ).first()
    assert row is not None
    patched = await client.patch(
        "/api/renovation/plan",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "col_015", "value": "1"}]},
    )
    assert patched.status_code == 200
    response = await client.delete(
        f"/api/renovation/dictionary/catalogs/criticality/options/{option.id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 409


async def test_catalog_option_mismatched_catalog_is_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    option = (
        await db_session.exec(
            select(RenovationReferenceValue).where(
                col(RenovationReferenceValue.catalog) == "criticality",
                col(RenovationReferenceValue.value_key) == "criticality_002",
            )
        )
    ).one()
    response = await client.delete(
        f"/api/renovation/dictionary/catalogs/work_type/options/{option.id}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
    )
    assert response.status_code == 404


# ─── Users ─────────────────────────────────────────────────────────────────


async def test_users_create_read_patch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={
            "email": "petrov@example.ru",
            "fullname": "Петров Пётр",
            "role": "asset_worker",
            "block": "tech",
            "assetNames": [R_ASSETS[0][2]],
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["email"] == "petrov@example.ru"
    assert created["role"] == "asset_worker"
    assert created["block"] == "tech"
    assert created["isActive"] is True
    assert created["assetNames"] == [R_ASSETS[0][2]]

    response = await client.patch(
        f"/api/renovation/dictionary/users/{created['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"isActive": False, "role": "admin"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["isActive"] is False
    assert updated["role"] == "admin"
    assert updated["block"] is None
    # Admin implicitly sees all assets — the grid shows an empty list.
    assert updated["assetNames"] == []
    assert updated["lastAuthenticatedAt"] is None

    rows = await client.get("/api/renovation/dictionary/users", headers=ADMIN_HEADERS)
    emails = [row["email"] for row in rows.json()]
    assert "petrov@example.ru" in emails


async def test_user_create_block_owner_requires_no_assets(client: AsyncClient) -> None:
    """A block owner only needs its responsibility block, not asset grants.

    Block owners (Техническая дирекция/Дирекция КСиР/Дирекция МТО) implicitly see all assets — the
    UI must not force the admin to grant them assets.
    """
    response = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={
            "email": "tashpaiev@example.ru",
            "fullname": "Технический",
            "role": "block_owner",
            "block": "tech",
            "assetNames": [],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["role"] == "block_owner"
    assert created["block"] == "tech"
    assert created["assetNames"] == []


async def test_user_create_block_required_for_worker(client: AsyncClient) -> None:
    response = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={"email": "bezbloka@example.ru", "fullname": "Без блока", "role": "asset_worker", "assetNames": []},
    )
    assert response.status_code == 409
    assert "блок" in response.json()["detail"]


async def test_user_create_duplicate_email_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={"email": "dupl@example.ru", "fullname": "Дубль", "role": "admin", "assetNames": []},
    )
    response = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={"email": "dupl@example.ru", "fullname": "Дубль 2", "role": "admin", "assetNames": []},
    )
    assert response.status_code == 409


async def test_user_delete_not_supported(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    created = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={"email": "temp@example.ru", "fullname": "Временный", "role": "admin", "assetNames": []},
    )
    user_id = created.json()["id"]
    response = await client.delete(f"/api/renovation/dictionary/users/{user_id}", headers=ADMIN_HEADERS)
    assert response.status_code == 405


# ─── Export ────────────────────────────────────────────────────────────────


async def test_dictionary_export_assets(client: AsyncClient) -> None:
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationAssets",
            "headerPinned": True,
            "fileName": "активы.xlsx",
            "columns": [
                {"key": "name", "headerPath": ["Актив"], "width": 20},
                {"key": "repairCode", "headerPath": ["Ремонтный код"], "width": 12},
            ],
            "rowIds": None,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    sheet = workbook["Активы"]
    assert sheet.cell(row=1, column=1).value == "Актив"
    assert sheet.cell(row=1, column=2).value == "Ремонтный код"
    assert sheet.max_row >= 3
    names = {sheet.cell(row=index, column=1).value for index in range(2, sheet.max_row + 1)}
    assert R_ASSETS[0][2] in names


async def test_dictionary_export_users_with_asset_dropdown(client: AsyncClient) -> None:
    # A realistic user-grid export: the role/block enums and the last-login
    # datetime columns were rejected earlier («скрытый или недоступный столбец»)
    # because the backend allow-list missed them.
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationUsers",
            "headerPinned": True,
            "fileName": "пользователи.xlsx",
            "columns": [
                {"key": "email", "headerPath": ["Email"], "width": 20},
                {"key": "fullname", "headerPath": ["ФИО"], "width": 28},
                {"key": "role", "headerPath": ["Роль"], "width": 20},
                {"key": "block", "headerPath": ["Блок"], "width": 18},
                {"key": "assetNames", "headerPath": ["Активы"], "width": 20},
                {"key": "isActive", "headerPath": ["Доступ к проекту"], "width": 14},
                {"key": "lastAuthenticatedAt", "headerPath": ["Время последнего входа"], "width": 22},
            ],
            "rowIds": None,
        },
    )
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    assert "Пользователи" in workbook.sheetnames
    assert "Списки" in workbook.sheetnames
    sheet = workbook["Пользователи"]
    assert sheet.cell(row=1, column=1).value == "Email"
    assert sheet.cell(row=1, column=3).value == "Роль"
    # One data row carries a non-empty role.
    roles = {sheet.cell(row=index, column=3).value for index in range(2, sheet.max_row + 1)}
    assert any(roles)


async def test_dictionary_export_catalog(client: AsyncClient) -> None:
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationCatalog:criticality",
            "headerPinned": True,
            "fileName": "критичность.xlsx",
            "columns": [{"key": "label", "headerPath": ["Значение"], "width": 20}],
            "rowIds": None,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    assert "Группа критичности оборудования" in workbook.sheetnames
    sheet = workbook["Группа критичности оборудования"]
    assert sheet.cell(row=1, column=1).value == "Значение"
    assert sheet.max_row >= 5
    labels = {sheet.cell(row=index, column=1).value for index in range(2, sheet.max_row + 1)}
    assert {"1", "2", "3", "4"} <= labels


async def test_dictionary_export_validation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationAssets",
            "headerPinned": True,
            "fileName": "bad",
            "columns": [{"key": "name", "headerPath": ["Актив"], "width": 20}],
            "rowIds": None,
        },
    )
    assert response.status_code == 422
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "unknown",
            "headerPinned": True,
            "fileName": "bad.xlsx",
            "columns": [{"key": "name", "headerPath": ["Актив"], "width": 20}],
            "rowIds": None,
        },
    )
    assert response.status_code == 422
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationAssets",
            "headerPinned": True,
            "fileName": "bad.xlsx",
            "columns": [{"key": "secret", "headerPath": ["Секрет"], "width": 20}],
            "rowIds": None,
        },
    )
    assert response.status_code == 422


async def test_catalog_option_create_is_undoable_and_redoable(client) -> None:
    """A created catalog option is undone (record gone) and redone (record back)."""
    response = await client.post(
        "/api/renovation/dictionary/catalogs/work_type/options",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"label": "Тестовая работа"},
    )
    assert response.status_code == 201
    created_id = response.json()["valueId"]

    undone = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationCatalog:work_type"},
    )
    assert undone.status_code == 200
    assert undone.json()["applied"] is True
    assert undone.json()["field"] == "__created"

    rows = (await client.get("/api/renovation/dictionary/catalogs/work_type", headers=ADMIN_HEADERS)).json()
    assert all(option["valueId"] != created_id for option in rows)

    redone = await client.post(
        "/api/renovation/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationCatalog:work_type"},
    )
    assert redone.status_code == 200
    assert redone.json()["applied"] is True
    rows = (await client.get("/api/renovation/dictionary/catalogs/work_type", headers=ADMIN_HEADERS)).json()
    assert any(option["valueId"] == created_id for option in rows)


async def test_catalog_option_duplicate_label_rejected(client) -> None:
    await client.post(
        "/api/renovation/dictionary/catalogs/work_type/options",
        headers=ADMIN_HEADERS,
        json={"label": "Уникальная работа"},
    )
    duplicate = await client.post(
        "/api/renovation/dictionary/catalogs/work_type/options",
        headers=ADMIN_HEADERS,
        json={"label": "уникальная работа"},
    )
    assert duplicate.status_code == 409
    assert "уже есть" in duplicate.json()["detail"]

    # Rename onto an existing label is rejected too.
    created = await client.post(
        "/api/renovation/dictionary/catalogs/work_type/options",
        headers=ADMIN_HEADERS,
        json={"label": "Другая уникальная работа"},
    )
    renamed = await client.patch(
        f"/api/renovation/dictionary/catalogs/work_type/options/{created.json()['valueId']}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
        json={"label": "Уникальная работа"},
    )
    assert renamed.status_code == 409
