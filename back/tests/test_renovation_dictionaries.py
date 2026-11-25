"""Tests for the «Ремонт» editable dictionaries: assets, catalog options, users, export."""

from io import BytesIO
from uuid import UUID

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project
from src.renovation.constants import R_ASSETS, RENOVATION_PROJECT_KEY
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.reference_data import reference_option_id

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


async def test_asset_rows_carry_the_plan_total(client: AsyncClient) -> None:
    """Each asset row carries the plan total and the number of repair works."""
    response = await client.get("/api/renovation/dictionary/assets", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    by_code = {row["repairCode"]: row for row in response.json()}
    # The fixture: bsk total_cost_rub_ex_vat = 10 + 20 = 30, vos = 5 + 7 = 12.
    assert by_code["BSKR"]["totalCost"] == 30.0
    assert by_code["VOZR"]["totalCost"] == 12.0
    # Each fixture asset has exactly one row in the working version.
    assert by_code["BSKR"]["repairCount"] == 1
    assert by_code["VOZR"]["repairCount"] == 1


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
    asset = await _asset_by_code(db_session, "BSKR")
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


async def test_catalog_options_are_ordered_by_label(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Catalogs read as dictionaries: alphabetical, whatever the source order was."""
    response = await client.get("/api/renovation/dictionary/catalogs/work_type", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    labels = [option["label"] for option in response.json()]
    assert labels == sorted(labels)
    assert labels[0] == "Вспомогательные работы (леса, изоляция и пр)"

    meta = (await client.get("/api/renovation/meta", headers=ADMIN_HEADERS)).json()
    work_types = next(catalog for catalog in meta["catalogs"] if catalog["id"] == "work_type")
    assert [option["label"] for option in work_types["options"]] == sorted(labels)


async def test_catalog_rows_carry_the_plan_aggregates(client: AsyncClient) -> None:
    """Every reference catalog row carries the same aggregates as «Предприятия»."""
    response = await client.get("/api/renovation/dictionary/catalogs/work_category", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    by_label = {row["label"]: row for row in response.json()}
    # The fixture: both plan rows carry «Остановочный капитальный ремонт»,
    # total_cost_rub_ex_vat = 30 + 12 → two repair works, 42 «Стоимость итого».
    used = by_label["Остановочный капитальный ремонт"]
    assert used["repairCount"] == 2
    assert used["totalCost"] == 42.0
    # An option no plan row uses aggregates to zero.
    assert by_label["ТОиР"]["repairCount"] == 0
    assert by_label["ТОиР"]["totalCost"] == 0.0


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
    # A fresh option is unused by the working plan → zero aggregates.
    assert created["repairCount"] == 0
    assert created["totalCost"] == 0.0

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
    option = await db_session.get(RenovationReferenceValue, reference_option_id("criticality", "1"))
    assert option is not None
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
        json={"updates": [{"rowId": str(row.id), "field": "equipment_criticality_group_id", "value": "1"}]},
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
    option = await db_session.get(RenovationReferenceValue, reference_option_id("criticality", "2"))
    assert option is not None
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
        json={"role": "blocked"},
    )
    assert response.status_code == 200
    updated = response.json()
    # The role is the source of truth: «Заблокирован» means no access and no
    # directorate/asset grants.
    assert updated["isActive"] is False
    assert updated["role"] == "blocked"
    assert updated["block"] is None
    assert updated["assetNames"] == []
    assert updated["lastAuthenticatedAt"] is None

    # A working role restores access; the directorate comes back with it.
    promoted = await client.patch(
        f"/api/renovation/dictionary/users/{created['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["isActive"] is True
    assert promoted.json()["block"] is None
    assert promoted.json()["assetNames"] == []

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


async def test_user_delete_removes_the_project_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    created = await client.post(
        "/api/renovation/dictionary/users",
        headers=ADMIN_HEADERS,
        json={"email": "temp@example.ru", "fullname": "Временный", "role": "admin", "assetNames": []},
    )
    user_id = created.json()["id"]
    # The protective layer: revoke access first, then delete.
    refused = await client.delete(f"/api/renovation/dictionary/users/{user_id}", headers=ADMIN_HEADERS)
    assert refused.status_code == 409

    revoked = await client.patch(
        f"/api/renovation/dictionary/users/{user_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": "users-session"},
        json={"isActive": False},
    )
    assert revoked.status_code == 200

    response = await client.delete(f"/api/renovation/dictionary/users/{user_id}", headers=ADMIN_HEADERS)
    assert response.status_code == 204


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
                {"key": "totalCost", "headerPath": ["Стоимость итого, руб. без НДС"], "width": 24},
                {"key": "repairCount", "headerPath": ["Число ремонтных работ"], "width": 18},
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
    assert sheet.cell(row=1, column=4).value == "Число ремонтных работ"
    assert sheet.max_row >= 3
    names = {sheet.cell(row=index, column=1).value for index in range(2, sheet.max_row + 1)}
    assert R_ASSETS[0][2] in names
    counts = {sheet.cell(row=index, column=4).value for index in range(2, sheet.max_row + 1)}
    assert 1 in counts


async def test_dictionary_export_assets_carries_summary_row(client: AsyncClient) -> None:
    """The «Предприятия» export mirrors the grid's pinned summary row.

    Row 2 (directly under the header) aggregates the numeric columns with a
    live SUM over the data; the non-aggregating columns merge their header
    cell down over it (one tall header cell, like the grid's summary band).
    """
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
                {"key": "totalCost", "headerPath": ["Стоимость итого, руб. без НДС"], "width": 24},
                {"key": "repairCount", "headerPath": ["Число ремонтных работ"], "width": 18},
            ],
            "rowIds": None,
            "valuesOnly": False,
        },
    )
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["Активы"]
    # The summary row sits directly under the flat header; the data start below it.
    assert sheet.cell(row=2, column=3).value is not None  # totalCost aggregate
    assert isinstance(sheet.cell(row=2, column=3).value, str)
    assert sheet.cell(row=2, column=3).value.startswith("=SUM(C3:C")
    assert sheet.cell(row=2, column=4).value is not None  # repairCount aggregate
    # Non-aggregating columns: the header cell merges down over the summary row.
    merges = [str(ref) for ref in sheet.merged_cells.ranges]
    assert "A1:A2" in merges
    assert "B1:B2" in merges
    # The frozen pane covers the header AND the summary row.
    assert sheet.freeze_panes == "A3"
    # Cached values: the seed's totals match the sum over the data rows.
    cached = load_workbook(BytesIO(response.content), data_only=True)["Активы"]
    expected = sum(
        value
        for value in (cached.cell(row=row, column=4).value for row in range(3, cached.max_row + 1))
        if isinstance(value, int | float)
    )
    assert cached.cell(row=2, column=4).value == expected


async def test_dictionary_export_other_sections_have_no_summary_row(client: AsyncClient) -> None:
    """Only the object-plan dictionaries pin a summary; the users table stays flat."""
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
            ],
            "rowIds": None,
        },
    )
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["Пользователи"]
    assert sheet.cell(row=2, column=1).value is not None  # data right under the header
    assert sheet.freeze_panes == "A2"
    merges = [str(ref) for ref in sheet.merged_cells.ranges]
    assert "A1:A2" not in merges


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


async def test_dictionary_export_catalog_carries_summary_row(client: AsyncClient) -> None:
    """A reference catalog export mirrors its pinned summary row too."""
    response = await client.post(
        "/api/renovation/dictionary/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "renovationCatalog:work_category",
            "headerPinned": True,
            "fileName": "категории.xlsx",
            "columns": [
                {"key": "label", "headerPath": ["Значение"], "width": 20},
                {"key": "totalCost", "headerPath": ["Стоимость итого, руб. без НДС"], "width": 24},
                {"key": "repairCount", "headerPath": ["Число ремонтных работ"], "width": 18},
            ],
            "rowIds": None,
            "valuesOnly": False,
        },
    )
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["Категория работ"]
    # The summary row sits directly under the flat header; the data start below.
    assert sheet.cell(row=2, column=2).value.startswith("=SUM(B3:B")
    assert sheet.cell(row=2, column=3).value.startswith("=SUM(C3:C")
    # The non-aggregating «Значение» header merges down over the summary row.
    merges = [str(ref) for ref in sheet.merged_cells.ranges]
    assert "A1:A2" in merges
    assert sheet.freeze_panes == "A3"
    # Cached values: both fixture rows carry this category (total_cost_rub_ex_vat = 30 + 12).
    cached = load_workbook(BytesIO(response.content), data_only=True)["Категория работ"]
    assert cached.cell(row=2, column=2).value == 42.0
    assert cached.cell(row=2, column=3).value == 2


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
