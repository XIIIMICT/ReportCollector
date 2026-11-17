"""The «Ставки» main table: validation, computed columns and undo."""

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlalchemy import text

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")

ORG = "00000000-0000-0000-0000-000000000101"
COUNTRY_RU = "00000000-0000-0000-0000-000000000201"
COUNTRY_BY = "00000000-0000-0000-0000-000000000202"
COUNTRY_DE = "00000000-0000-0000-0000-000000000203"
COUNTRY_ARCHIVED = "00000000-0000-0000-0000-000000000204"
TRANSPORT_AUTO = "00000000-0000-0000-0000-000000000301"
TRANSPORT_RAIL = "00000000-0000-0000-0000-000000000302"
TRANSPORT_SEA = "00000000-0000-0000-0000-000000000303"
VEHICLE_TENT = "00000000-0000-0000-0000-000000000401"
VEHICLE_GONDOLA = "00000000-0000-0000-0000-000000000403"
PERIOD = "2026-11-01"


def payload(**overrides: object) -> dict[str, object]:
    values = {
        "periodDate": PERIOD,
        "assetId": ORG,
        "hazardClassValue": None,
        "countryFromId": COUNTRY_RU,
        "countryToId": COUNTRY_DE,
        "transportTypeId": TRANSPORT_AUTO,
        "vehicleTypeId": VEHICLE_TENT,
        "loadingTons": 10.5,
        "freightRateRub": 100.0,
        "pprRateRub": 200.0,
        "deliveryRateRub": 300.0,
        "netRateRub": 400.0,
        "priceKind": "INDICATIVE",
        "note": None,
    }
    values.update(overrides)
    return values


async def create(client: AsyncClient, **overrides: object) -> dict[str, object]:
    response = await client.post(
        "/api/logistics/rates",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json=payload(**overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_rates_are_admin_only(client: AsyncClient) -> None:
    response = await client.get("/api/logistics/rates", headers=VIEWER_HEADERS, params={"month": 11, "year": 2026})
    assert response.status_code == 403


async def test_create_reads_computed_columns(client: AsyncClient) -> None:
    row = await create(client)
    assert row["assetName"] == "БСК"
    assert row["countryFromName"] == "Россия"
    assert row["transportTypeName"] == "Автомобильный"
    assert row["vehicleTypeName"] == "Тент"
    assert row["direction"] == "ДЗ"
    assert row["dangerous"] is False
    assert row["freightRateRub"] == 100.0
    # The dollar cells are rub × 83.90 and are never stored.
    assert row["freightRateUsd"] == 8390.0
    assert row["pprRateUsd"] == 16780.0
    assert row["deliveryRateUsd"] == 25170.0
    assert row["netRateUsd"] == 33560.0

    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert view.status_code == 200
    body = view.json()
    assert body["usdRate"] == 83.9
    assert [item["id"] for item in body["rows"]] == [row["id"]]


async def test_direction_uses_the_destination_market_for_russian_departures(client: AsyncClient) -> None:
    # From Russia the direction is the DESTINATION market: РФ/БЗ/ДЗ.
    home = await create(client, countryFromId=COUNTRY_RU, countryToId=COUNTRY_RU)
    assert home["direction"] == "РФ"
    near_abroad = await create(client, countryFromId=COUNTRY_RU, countryToId=COUNTRY_BY)
    assert near_abroad["direction"] == "БЗ"
    far_abroad = await create(client, countryFromId=COUNTRY_RU, countryToId=COUNTRY_DE)
    assert far_abroad["direction"] == "ДЗ"
    # Any other departure is an import when it arrives in Russia, else an export.
    imported = await create(client, countryFromId=COUNTRY_DE, countryToId=COUNTRY_RU)
    assert imported["direction"] == "Импорт"
    exported = await create(client, countryFromId=COUNTRY_DE, countryToId=COUNTRY_BY)
    assert exported["direction"] == "Экспорт"


async def test_hazard_class_makes_the_row_dangerous(client: AsyncClient) -> None:
    row = await create(client)
    patched = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json={"field": "hazardClassValue", "value": 3},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["dangerous"] is True
    assert patched.json()["hazardClassValue"] == 3
    cleared = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json={"field": "hazardClassValue", "value": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["dangerous"] is False


async def test_vehicle_type_depends_on_the_transport_type(client: AsyncClient) -> None:
    row = await create(client)
    # Rail has its own vehicle set: the tent is cleared on the transport change.
    moved = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "transportTypeId", "value": TRANSPORT_RAIL},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["vehicleTypeId"] is None
    wrong = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "vehicleTypeId", "value": VEHICLE_TENT},
    )
    assert wrong.status_code == 400
    allowed = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "vehicleTypeId", "value": VEHICLE_GONDOLA},
    )
    assert allowed.status_code == 200
    assert allowed.json()["vehicleTypeName"] == "Полувагон"


async def test_a_transport_without_mappings_keeps_vehicle_null(client: AsyncClient) -> None:
    row = await create(client, transportTypeId=TRANSPORT_SEA, vehicleTypeId=None)
    assert row["vehicleTypeId"] is None
    assert row["vehicleTypeName"] == ""
    invalid = await client.patch(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "vehicleTypeId", "value": VEHICLE_TENT},
    )
    assert invalid.status_code == 400


async def test_negative_or_precise_numbers_are_rejected(client: AsyncClient) -> None:
    for field, value in (
        ("loadingTons", -1),
        ("loadingTons", 1.234),
        ("freightRateRub", -0.01),
        ("pprRateRub", 1.234),
    ):
        response = await client.post(
            "/api/logistics/rates",
            headers=ADMIN_HEADERS,
            json=payload(**{field: value}),
        )
        assert response.status_code == 422, (field, value, response.text)


async def test_archived_references_are_rejected_and_shown_deleted(client: AsyncClient, db_session) -> None:
    rejected = await client.post(
        "/api/logistics/rates",
        headers=ADMIN_HEADERS,
        json=payload(countryFromId=COUNTRY_ARCHIVED),
    )
    assert rejected.status_code == 400

    row = await create(client)
    await db_session.execute(
        text("UPDATE countries SET deleted_at = now() WHERE id = :id"),
        {"id": COUNTRY_DE},
    )
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert view.status_code == 200
    shown = next(item for item in view.json()["rows"] if item["id"] == row["id"])
    assert shown["countryToName"] == "УДАЛЕНО: Германия"


async def test_missing_dict_schema_is_a_readable_503(client: AsyncClient, monkeypatch) -> None:
    """Without the corporate schema the form says so instead of crashing on SQL."""
    monkeypatch.setattr("src.logistics.rates.LOGISTICS_SOURCE_SCHEMA", "rc_absent_dict")
    response = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert response.status_code == 503
    assert "LOGISTICS_DICT_SCHEMA" in response.json()["detail"]


async def test_delete_removes_the_row_and_is_undoable(client: AsyncClient) -> None:
    row = await create(client)
    other = await create(client, countryToId=COUNTRY_BY)
    deleted = await client.delete(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
    )
    assert deleted.status_code == 204, deleted.text
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert [item["id"] for item in view.json()["rows"]] == [other["id"]]

    again = await client.delete(
        f"/api/logistics/rates/{row['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
    )
    assert again.status_code == 404

    undone = await client.post(
        "/api/logistics/rates/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json={"section": "logisticsRates", "reportMonth": PERIOD},
    )
    assert undone.json()["applied"] is True
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert [item["id"] for item in view.json()["rows"]] == [row["id"], other["id"]]


async def test_rows_are_listed_in_creation_order(client: AsyncClient) -> None:
    first = await create(client)
    second = await create(client, countryToId=COUNTRY_BY)
    third = await create(client, countryToId=COUNTRY_BY)
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert [item["id"] for item in view.json()["rows"]] == [first["id"], second["id"], third["id"]]


async def test_create_is_undoable_and_redoable(client: AsyncClient) -> None:
    row = await create(client)
    state = await client.get(
        "/api/logistics/rates/undo-state",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test", "period_date": PERIOD},
    )
    assert state.json()["canUndo"] is True
    undone = await client.post(
        "/api/logistics/rates/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json={"section": "logisticsRates", "reportMonth": PERIOD},
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["applied"] is True
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert all(item["id"] != row["id"] for item in view.json()["rows"])
    redone = await client.post(
        "/api/logistics/rates/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": "rates-test"},
        json={"section": "logisticsRates", "reportMonth": PERIOD},
    )
    assert redone.status_code == 200, redone.text
    assert redone.json()["applied"] is True
    view = await client.get("/api/logistics/rates", headers=ADMIN_HEADERS, params={"month": 11, "year": 2026})
    assert any(item["id"] == row["id"] for item in view.json()["rows"])
