"""Excel export of the «Сырьё» tables: formulas, formats, validation, permissions."""

from datetime import datetime
from io import BytesIO
from uuid import UUID

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import ProjectRole, User
from src.auth.service import activate_project_user, grant_project_assets, grant_project_role


def admin_headers() -> dict[str, str]:
    return {
        "email": "admin@example.ru",
        "preferred_username": "admin",
        "subject": "admin-sub",
        "email_verified": "true",
    }


def worker_headers() -> dict[str, str]:
    return {
        "email": "worker@example.ru",
        "preferred_username": "worker",
        "subject": "worker-sub",
        "email_verified": "true",
    }


def movement_columns() -> list[dict]:
    return [
        {"key": "rawMaterialName", "headerPath": ["Сырьё"], "width": 20.0},
        {"key": "assetName", "headerPath": ["Актив"], "width": 12.0},
        {"key": "demandPlanTons", "headerPath": ["Спрос, план"], "width": 14.0},
        {"key": "consumptionFactTons", "headerPath": ["Спрос, факт"], "width": 14.0},
        {"key": "demandDeviationTons", "headerPath": ["Отклонение"], "width": 14.0},
        {"key": "deliveryPlanTons", "headerPath": ["Поставка, план"], "width": 14.0},
        {"key": "deliveryFactTons", "headerPath": ["Поставка, факт"], "width": 14.0},
        {"key": "deliveryDeviationTons", "headerPath": ["Отклонение"], "width": 14.0},
    ]


async def test_export_movement_workbook(client) -> None:
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "headerPinned": True,
            "columns": movement_columns(),
            "fileName": "Движение сырья — август (13.08.2026, 00.45.12).xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Движение сырья"]
    sheet = workbook.active
    assert sheet.freeze_panes == "A2"

    assert [sheet.cell(row=1, column=column).value for column in range(1, 9)] == [
        "Сырьё",
        "Актив",
        "Спрос, план",
        "Спрос, факт",
        "Отклонение",
        "Поставка, план",
        "Поставка, факт",
        "Отклонение",
    ]
    assert sheet.max_row == 5
    row_index = next(
        index
        for index in range(2, 6)
        if sheet.cell(row=index, column=1).value == "Аммиак" and sheet.cell(row=index, column=2).value == "СНХЗ"
    )
    assert sheet.cell(row=row_index, column=3).value == 100.0
    assert sheet.cell(row=row_index, column=3).number_format == "General"
    assert sheet.cell(row=row_index, column=3).border.left.style == "thin"
    assert sheet.cell(row=row_index, column=3).border.right.style == "thin"
    assert sheet.cell(row=row_index, column=3).border.top.style == "thin"
    assert sheet.cell(row=row_index, column=3).border.bottom.style == "thin"
    assert sheet.cell(row=row_index, column=5).border.left.style == "thin"
    assert sheet.cell(row=1, column=1).border.bottom.style == "thin"
    assert sheet.cell(row=row_index, column=4).value == 80.0
    assert sheet.cell(row=row_index, column=5).value == f"=C{row_index}-D{row_index}"
    assert sheet.cell(row=row_index, column=6).value == 90.0
    assert sheet.cell(row=row_index, column=7).value == 70.0
    assert sheet.cell(row=row_index, column=8).value == f"=F{row_index}-G{row_index}"


async def test_export_deviation_without_operands_uses_value(client) -> None:
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "columns": [
                {"key": "rawMaterialName", "headerPath": ["Сырьё"]},
                {"key": "demandDeviationTons", "headerPath": ["Отклонение"]},
            ],
            "fileName": "отклонение.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content)).active
    value = next(
        sheet.cell(row=index, column=2).value
        for index in range(2, sheet.max_row + 1)
        if sheet.cell(row=index, column=2).value is not None
    )
    assert value == 20.0


async def test_export_grouped_headers_merge(client) -> None:
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "headerPinned": False,
            "columns": [
                {"key": "rawMaterialName", "headerPath": ["Сырьё", "Наименование"]},
                {"key": "assetName", "headerPath": ["Сырьё", "Актив"]},
                {"key": "demandPlanTons", "headerPath": ["План", "Спрос"]},
                {"key": "consumptionFactTons", "headerPath": ["Факт", "Спрос"]},
            ],
            "fileName": "группировка.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet.freeze_panes is None
    assert sheet.cell(row=1, column=1).value == "Сырьё"
    assert sheet.cell(row=2, column=1).value == "Наименование"
    assert sheet.cell(row=2, column=2).value == "Актив"
    assert "A1:B1" in [str(item) for item in sheet.merged_cells.ranges]
    assert sheet.row_dimensions[1].height == 34.0


async def test_export_visible_rows_order(client) -> None:
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-09-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()
    assert len(rows) == 4
    ids = [row["id"] for row in rows]
    missing = str(UUID(int=0))

    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-09-01",
            "columns": [{"key": "rawMaterialName", "headerPath": ["Сырьё"]}],
            "rowIds": [ids[2], missing, ids[0]],
            "fileName": "видимое.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet.max_row == 3
    assert [sheet.cell(row=row, column=1).value for row in range(2, 4)] == [
        rows[2]["rawMaterialName"],
        rows[0]["rawMaterialName"],
    ]


async def test_export_prices_reference_validation(client) -> None:
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "rawMaterialPrices",
            "columns": [
                {"key": "rawMaterialName", "headerPath": ["Сырьё"]},
                {"key": "deliveryBasisName", "headerPath": ["Базис"]},
                {"key": "businessPlanUsdPerTon", "headerPath": ["USD/т"]},
            ],
            "fileName": "цены.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Цены", "Списки"]
    lists = workbook["Списки"]
    assert lists.sheet_state == "hidden"
    assert [lists.cell(row=row, column=1).value for row in range(1, 4)] == ["БСЗ", "ВОС", "СНХЗ"]
    assert [lists.cell(row=row, column=2).value for row in range(1, 3)] == ["FCA", "FOB"]

    sheet = workbook["Цены"]
    validations = sheet.data_validations.dataValidation
    assert len(validations) == 1
    assert validations[0].type == "list"
    assert validations[0].formula1 == "=Списки!$B$1:$B$2"
    assert max(item.max_row for item in validations[0].sqref.ranges) == 5


async def test_export_asset_column_restricted_for_single_asset(client, db_session) -> None:
    headers = worker_headers()
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ"])
    await db_session.commit()

    hidden = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "columns": movement_columns(),
            "fileName": "движение.xlsx",
        },
        headers=signed_identity_headers(**headers),
    )
    assert hidden.status_code == 422
    assert "скрытый" in hidden.json()["detail"]

    visible = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "columns": [column for column in movement_columns() if column["key"] != "assetName"],
            "fileName": "движение.xlsx",
        },
        headers=signed_identity_headers(**headers),
    )
    assert visible.status_code == 200


async def test_export_dictionary_section_requires_admin(client, db_session) -> None:
    headers = worker_headers()
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ"])
    await db_session.commit()

    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "rawMaterials",
            "columns": [{"key": "name", "headerPath": ["Наименование"]}],
            "fileName": "позиции.xlsx",
        },
        headers=signed_identity_headers(**headers),
    )
    assert response.status_code == 403


async def test_export_service_columns(client) -> None:
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "columns": [
                {"key": "rawMaterialName", "headerPath": ["Сырьё"]},
                {"key": "createdAt", "headerPath": ["Время добавления"]},
                {"key": "updatedAt", "headerPath": ["Время редактирования"]},
                {"key": "updatedBy", "headerPath": ["Автор последней редакции"]},
            ],
            "fileName": "служебные.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert response.status_code == 200

    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook.active
    assert sheet.cell(row=1, column=2).value == "Время добавления"
    assert sheet.cell(row=1, column=3).value == "Время редактирования"
    assert sheet.cell(row=1, column=4).value == "Автор последней редакции"
    assert isinstance(sheet.cell(row=2, column=2).value, datetime)
    assert sheet.cell(row=2, column=2).number_format == "DD.MM.YYYY HH:MM:SS"


async def test_export_users_accepts_last_authenticated(client: AsyncClient) -> None:
    """The «Пользователи» dictionary export passes on-screen columns.

    The grid shows the «Время последнего входа» column (lastAuthenticatedAt),
    which used to be rejected as a hidden column — exports of the users table
    were impossible.
    """
    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "users",
            "columns": [
                {"key": "email", "headerPath": ["Email"], "width": 22},
                {"key": "fullname", "headerPath": ["ФИО"], "width": 28},
                {"key": "isAdmin", "headerPath": ["Админ"], "width": 8},
                {"key": "assetNames", "headerPath": ["Активы"], "width": 20},
                {"key": "isActive", "headerPath": ["Доступ к проекту"], "width": 14},
                {"key": "lastAuthenticatedAt", "headerPath": ["Время последнего входа"], "width": 22},
            ],
            "fileName": "пользователи.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    assert workbook.sheetnames[0] == "Пользователи"


async def test_export_validation_errors(client) -> None:
    bad_filename = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "columns": [{"key": "rawMaterialName", "headerPath": ["Сырьё"]}],
            "fileName": "движение.txt",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert bad_filename.status_code == 422

    duplicate_keys = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "columns": [
                {"key": "rawMaterialName", "headerPath": ["Сырьё"]},
                {"key": "rawMaterialName", "headerPath": ["Сырьё"]},
            ],
            "fileName": "дубли.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert duplicate_keys.status_code == 422

    hidden_column = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "columns": [{"key": "unknownKey", "headerPath": ["Неизвестно"]}],
            "fileName": "неизвестно.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert hidden_column.status_code == 422


async def test_export_big_table_becomes_background_job(client, monkeypatch) -> None:
    """Past the inline threshold the export returns 202 + a job description, and the file endpoint streams the workbook."""
    import src.common.export_jobs as jobs_module

    monkeypatch.setattr(jobs_module, "INLINE_EXPORT_MAX_ROWS", 2)

    response = await client.post(
        "/api/raw-materials/export",
        json={
            "section": "materialMovement",
            "reportMonth": "2026-08-01",
            "headerPinned": True,
            "columns": movement_columns(),
            "fileName": "движение большое.xlsx",
        },
        headers=signed_identity_headers(**admin_headers()),
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] in {"pending", "running", "done"}
    assert job["fileName"].endswith(".xlsx")

    file_response = await client.get(
        f"/api/raw-materials/export/jobs/{job['id']}/file",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert file_response.status_code == 200
    assert file_response.content[:2] == b"PK"
    sheet = load_workbook(BytesIO(file_response.content)).active
    assert sheet.max_row == 5  # 1 header row + 4 seeded movement rows
    assert sheet.cell(row=2, column=5).value == "=C2-D2"

    status_response = await client.get(
        f"/api/raw-materials/export/jobs/{job['id']}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "done"
