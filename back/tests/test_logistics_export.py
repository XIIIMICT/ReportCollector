"""Excel export of the «Логистика» grids: the rates table scoped by role, the dictionaries."""

from io import BytesIO

from conftest import signed_identity_headers
from httpx2 import AsyncClient, Response
from openpyxl import load_workbook

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
REVIEWER_HEADERS = signed_identity_headers(email="reviewer@example.ru", name="Reviewer User")
WORKER_HEADERS = signed_identity_headers(email="worker@example.ru", name="Worker User")

ORG_BSK = "00000000-0000-0000-0000-000000000101"
ORG_SODA = "00000000-0000-0000-0000-000000000102"
COUNTRY_RU = "00000000-0000-0000-0000-000000000201"
COUNTRY_DE = "00000000-0000-0000-0000-000000000203"
TRANSPORT_AUTO = "00000000-0000-0000-0000-000000000301"
VEHICLE_TENT = "00000000-0000-0000-0000-000000000401"
PERIOD = "2026-11-01"

RATE_COLUMNS = [
    {"key": "assetName", "headerPath": ["Актив"], "width": 20},
    {"key": "direction", "headerPath": ["Направление"], "width": 14},
    {"key": "loadingTons", "headerPath": ["Загрузка в ТС, т"], "width": 14},
]


async def _add_user(client: AsyncClient, email: str, role: str, assets: list[str] | None = None) -> None:
    response = await client.post(
        "/api/logistics/users",
        headers=ADMIN_HEADERS,
        json={"email": email, "fullname": email, "role": role, "assetIds": assets or []},
    )
    assert response.status_code == 201, response.text


async def _create_rate(client: AsyncClient, *, asset: str = ORG_BSK, tons: float = 20) -> dict:
    response = await client.post(
        "/api/logistics/rates",
        headers=ADMIN_HEADERS,
        params={"session_id": "export-test"},
        json={
            "periodDate": PERIOD,
            "assetId": asset,
            "hazardClassValue": None,
            "countryFromId": COUNTRY_RU,
            "countryToId": COUNTRY_DE,
            "transportTypeId": TRANSPORT_AUTO,
            "vehicleTypeId": VEHICLE_TENT,
            "loadingTons": tons,
            "freightRateRub": 100,
            "pprRateRub": 0,
            "deliveryRateRub": 0,
            "netRateRub": 100,
            "priceKind": "INDICATIVE",
            "note": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _export(client: AsyncClient, payload: dict, headers: dict[str, str] | None = None) -> Response:
    body = {
        "headerPinned": True,
        "rowIds": None,
        "fileName": "Ставки.xlsx",
        **payload,
    }
    return await client.post("/api/logistics/export", headers=headers or ADMIN_HEADERS, json=body)


async def test_rates_export_renders_the_grid_columns(client: AsyncClient) -> None:
    await _create_rate(client, tons=20)
    response = await _export(
        client,
        {
            "section": "logisticsRates",
            "month": 11,
            "year": 2026,
            "columns": RATE_COLUMNS,
            "fileName": "Ставки.xlsx",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet.title == "Ставки"
    assert [cell.value for cell in sheet[1]] == ["Актив", "Направление", "Загрузка в ТС, т"]
    assert [cell.value for cell in sheet[2]] == ["БСК", "ДЗ", 20]


async def test_rates_export_honours_the_visible_rows(client: AsyncClient) -> None:
    first = await _create_rate(client, tons=11)
    second = await _create_rate(client, tons=22)
    response = await _export(
        client,
        {
            "section": "logisticsRates",
            "month": 11,
            "year": 2026,
            "columns": RATE_COLUMNS,
            "rowIds": [second["id"]],
        },
    )
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content)).active
    rows = [cell.value for cell in sheet[2]]
    assert rows[2] == 22
    assert first["id"] != second["id"]


async def test_a_worker_exports_only_their_assets(client: AsyncClient) -> None:
    await _create_rate(client, asset=ORG_BSK, tons=11)
    await _create_rate(client, asset=ORG_SODA, tons=22)
    await _add_user(client, "worker@example.ru", "asset_worker", [ORG_BSK])
    response = await _export(
        client,
        {
            "section": "logisticsRates",
            "month": 11,
            "year": 2026,
            "columns": RATE_COLUMNS,
        },
        headers=WORKER_HEADERS,
    )
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet.max_row == 2  # the header + the worker's own asset only
    assert sheet[2][0].value == "БСК"


async def test_dictionary_export_renders_countries(client: AsyncClient) -> None:
    response = await _export(
        client,
        {
            "section": "logisticsCountries",
            "columns": [
                {"key": "market", "headerPath": ["Рынок"], "width": 10},
                {"key": "shortname", "headerPath": ["Страна"], "width": 20},
            ],
        },
    )
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet.title == "Страны"
    values = [(row[0].value, row[1].value) for row in sheet.iter_rows(min_row=2)]
    assert ("РФ", "Россия") in values


async def test_users_export_is_admin_only(client: AsyncClient) -> None:
    await _add_user(client, "reviewer@example.ru", "reviewer")
    response = await _export(
        client,
        {
            "section": "logisticsUsers",
            "columns": [{"key": "email", "headerPath": ["Email"], "width": 20}],
        },
        headers=REVIEWER_HEADERS,
    )
    assert response.status_code == 403


async def test_export_rejects_hidden_columns_and_missing_period(client: AsyncClient) -> None:
    hidden = await _export(
        client,
        {
            "section": "logisticsRates",
            "month": 11,
            "year": 2026,
            "columns": [{"key": "secret", "headerPath": ["Секрет"], "width": 10}],
        },
    )
    assert hidden.status_code == 400
    period = await _export(
        client,
        {"section": "logisticsRates", "columns": RATE_COLUMNS},
    )
    assert period.status_code == 400
    assert "месяц" in period.json()["detail"].lower() or "год" in period.json()["detail"].lower()
