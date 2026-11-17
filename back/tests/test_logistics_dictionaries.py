"""Tests for the «Логистика» dictionaries read from the external `dict` schema."""

# ruff: noqa: S608 — every interpolated identifier is a generated test schema.

from datetime import UTC, datetime
from uuid import uuid4

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlmodel import col, select

from src.auth.models import Project
from src.logistics.constants import (
    FALLBACK_ORG_PREFIXES,
    FALLBACK_TRANSPORT_TYPES,
    LOGISTICS_PROJECT_KEY,
)
from src.logistics.models import LogisticsHazardClass, LogisticsPriceKind

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")

# One instant, used as the `deleted_at` of every archived fixture row.
ARCHIVED_AT = datetime(2026, 1, 1, tzinfo=UTC)

DICTIONARY_PATHS = (
    "/api/logistics/dictionaries/countries",
    "/api/logistics/dictionaries/transport-types",
    "/api/logistics/dictionaries/orgs",
    "/api/logistics/dictionaries/hazard-classes",
    "/api/logistics/users",
)


def _use_absent_schema(monkeypatch) -> str:
    """Point the reader at a throwaway schema that certainly does not exist.

    The real `dict` schema may exist on a developer's database (and must never
    be created or dropped by the tests), so every test works in its own
    `rc_test_dict_*` schema. DDL is transactional here: the outer test
    transaction rolls it back on teardown.
    """
    schema = f"rc_test_dict_{uuid4().hex[:10]}"
    monkeypatch.setattr("src.logistics.dictionaries.LOGISTICS_SOURCE_SCHEMA", schema)
    return schema


async def _create_schema(session, schema: str) -> None:
    await session.execute(text(f'CREATE SCHEMA "{schema}"'))


# ─── Access ────────────────────────────────────────────────────────────────


async def test_dictionaries_require_the_project_admin(client: AsyncClient) -> None:
    for path in DICTIONARY_PATHS:
        response = await client.get(path, headers=VIEWER_HEADERS)
        assert response.status_code == 403, path
        assert "Логистика" in response.json()["detail"]


async def test_the_logistics_project_and_price_kind_enum_exist(db_session) -> None:
    project = (await db_session.exec(select(Project).where(col(Project.key) == LOGISTICS_PROJECT_KEY))).one()
    assert project.name == "Логистика"
    labels = (
        await db_session.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND t.typname = 'logistics_price_kind' "
                "ORDER BY e.enumsortorder"
            )
        )
    ).all()
    assert [row.enumlabel for row in labels] == ["INDICATIVE", "PRICE"]
    assert [kind.value for kind in LogisticsPriceKind] == ["INDICATIVE", "PRICE"]
    hazard = (await db_session.exec(select(LogisticsHazardClass.value).order_by(col(LogisticsHazardClass.value)))).all()
    assert hazard == list(range(1, 10))


async def test_hazard_classes_are_seeded_1_to_9(client: AsyncClient) -> None:
    response = await client.get("/api/logistics/dictionaries/hazard-classes", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    rows = response.json()
    assert [row["value"] for row in rows] == list(range(1, 10))
    assert rows[0]["name"] == "1 класс"
    assert rows[-1]["name"] == "9 класс"


# ─── Fallback values (the external table is absent) ────────────────────────


async def test_countries_fall_back_to_the_demo_list(client: AsyncClient, monkeypatch) -> None:
    _use_absent_schema(monkeypatch)
    response = await client.get("/api/logistics/dictionaries/countries", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert [(row["shortname"], row["market"]) for row in body["rows"]] == [
        ("Россия", "РФ"),
        ("Беларусь", "БЗ"),
        ("Казахстан", "БЗ"),
        ("Германия", "ДЗ"),
    ]
    assert all(row["id"].startswith("fallback-country:") for row in body["rows"])


async def test_transport_falls_back_to_the_demo_matrix(client: AsyncClient, monkeypatch) -> None:
    _use_absent_schema(monkeypatch)
    response = await client.get("/api/logistics/dictionaries/transport-types", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert [(row["transportType"], row["vehicleType"]) for row in body["rows"]] == [
        (transport, vehicle) for transport, vehicles in FALLBACK_TRANSPORT_TYPES for vehicle in vehicles
    ]


async def test_orgs_fall_back_to_the_demo_list(client: AsyncClient, monkeypatch) -> None:
    _use_absent_schema(monkeypatch)
    response = await client.get("/api/logistics/dictionaries/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert [row["prefixRu"] for row in body["rows"]] == list(FALLBACK_ORG_PREFIXES)


# ─── External rows ─────────────────────────────────────────────────────────


async def test_countries_read_shortnames_and_market(client: AsyncClient, db_session, monkeypatch) -> None:
    schema = _use_absent_schema(monkeypatch)
    await _create_schema(db_session, schema)
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".countries '
            "(id uuid PRIMARY KEY, shortname text NOT NULL, alpha2 text NULL, "
            "ts boolean NULL, sng boolean NULL, deleted_at timestamptz NULL)"
        )
    )
    rows = [
        (uuid4(), "Россия", "RU", False, False, None),
        (uuid4(), "Казахстан", "KZ", True, False, None),
        (uuid4(), "Беларусь", "BY", False, True, None),
        (uuid4(), "Германия", "DE", False, False, None),
        (uuid4(), "Архив", "RU", False, False, ARCHIVED_AT),
    ]
    for row_id, shortname, alpha2, ts, sng, deleted_at in rows:
        await db_session.execute(
            text(
                f'INSERT INTO "{schema}".countries '
                "(id, shortname, alpha2, ts, sng, deleted_at) "
                "VALUES (:id, :shortname, :alpha2, :ts, :sng, :deleted_at)"
            ).bindparams(
                id=row_id,
                shortname=shortname,
                alpha2=alpha2,
                ts=ts,
                sng=sng,
                deleted_at=deleted_at,
            ),
        )
    response = await client.get("/api/logistics/dictionaries/countries", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "external"
    assert [(row["shortname"], row["market"]) for row in body["rows"]] == [
        ("Россия", "РФ"),
        ("Беларусь", "БЗ"),
        ("Казахстан", "БЗ"),
        ("Германия", "ДЗ"),
    ]


async def test_an_empty_external_table_is_not_replaced_by_the_fallback(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    schema = _use_absent_schema(monkeypatch)
    await _create_schema(db_session, schema)
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".countries '
            "(id uuid PRIMARY KEY, shortname text NOT NULL, alpha2 text NULL, "
            "ts boolean NULL, sng boolean NULL, deleted_at timestamptz NULL)"
        )
    )
    response = await client.get("/api/logistics/dictionaries/countries", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"source": "external", "rows": []}


async def test_transport_types_are_grouped_and_sorted(client: AsyncClient, db_session, monkeypatch) -> None:
    schema = _use_absent_schema(monkeypatch)
    await _create_schema(db_session, schema)
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".transport_types '
            "(id uuid PRIMARY KEY, code text NOT NULL, shortname text NOT NULL, "
            "fullname text NOT NULL, deleted_at timestamptz NULL)"
        )
    )
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".vehicle_types '
            "(id uuid PRIMARY KEY, name text NOT NULL, deleted_at timestamptz NULL)"
        )
    )
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".transport_types_vehicle_types '
            "(id uuid PRIMARY KEY, transport_type_id uuid NOT NULL, "
            "vehicle_type_id uuid NOT NULL, deleted_at timestamptz NULL)"
        )
    )
    road, auto, sea, archived = uuid4(), uuid4(), uuid4(), uuid4()
    for transport_id, shortname, deleted_at in (
        (road, "Железнодорожный", None),
        (auto, "Автомобильный", None),
        (sea, "Морской", None),
        (archived, "Архивный", ARCHIVED_AT),
    ):
        await db_session.execute(
            text(
                f'INSERT INTO "{schema}".transport_types '
                "(id, code, shortname, fullname, deleted_at) "
                "VALUES (:id, :code, :shortname, :fullname, :deleted_at)"
            ).bindparams(
                id=transport_id,
                code=shortname,
                shortname=shortname,
                fullname=f"{shortname} транспорт",
                deleted_at=deleted_at,
            )
        )
    tank, gondola, tent, deleted_vehicle = uuid4(), uuid4(), uuid4(), uuid4()
    for vehicle_id, name, deleted_at in (
        (tank, "Цистерна", None),
        (gondola, "Полувагон", None),
        (tent, "Тент", None),
        (deleted_vehicle, "Удалённый", ARCHIVED_AT),
    ):
        await db_session.execute(
            text(
                f'INSERT INTO "{schema}".vehicle_types (id, name, deleted_at) VALUES (:id, :name, :deleted_at)'
            ).bindparams(id=vehicle_id, name=name, deleted_at=deleted_at)
        )
    for transport_id, vehicle_id, deleted_at in (
        (road, tank, None),
        (road, gondola, None),
        (auto, tent, None),
        # A deleted mapping and a mapping to a deleted vehicle are ignored.
        (auto, gondola, ARCHIVED_AT),
        (auto, deleted_vehicle, None),
        (archived, tent, None),
    ):
        await db_session.execute(
            text(
                f'INSERT INTO "{schema}".transport_types_vehicle_types '
                "(id, transport_type_id, vehicle_type_id, deleted_at) "
                "VALUES (:id, :transport_id, :vehicle_id, :deleted_at)"
            ).bindparams(
                id=uuid4(),
                transport_id=transport_id,
                vehicle_id=vehicle_id,
                deleted_at=deleted_at,
            )
        )
    response = await client.get("/api/logistics/dictionaries/transport-types", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "external"
    assert [(row["transportType"], row["vehicleType"]) for row in body["rows"]] == [
        ("Автомобильный", "Тент"),
        ("Железнодорожный", "Полувагон"),
        ("Железнодорожный", "Цистерна"),
        # A type without mappings keeps its row, with an empty vehicle type.
        ("Морской", ""),
    ]
    # Consecutive rows of one type share the id the grid merges on.
    assert body["rows"][1]["transportTypeId"] == body["rows"][2]["transportTypeId"]
    assert body["rows"][0]["transportTypeId"] != body["rows"][1]["transportTypeId"]


async def test_orgs_read_all_prefixes(client: AsyncClient, db_session, monkeypatch) -> None:
    schema = _use_absent_schema(monkeypatch)
    await _create_schema(db_session, schema)
    await db_session.execute(
        text(
            f'CREATE TABLE "{schema}".orgs (id uuid PRIMARY KEY, prefix_ru text NOT NULL, deleted_at timestamptz NULL)'
        )
    )
    for org_id, prefix, deleted_at in (
        (uuid4(), "Сода", None),
        (uuid4(), "БСК", None),
        (uuid4(), "Архив", ARCHIVED_AT),
    ):
        await db_session.execute(
            text(
                f'INSERT INTO "{schema}".orgs (id, prefix_ru, deleted_at) VALUES (:id, :prefix, :deleted_at)'
            ).bindparams(id=org_id, prefix=prefix, deleted_at=deleted_at)
        )
    response = await client.get("/api/logistics/dictionaries/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "external"
    assert [row["prefixRu"] for row in body["rows"]] == ["БСК", "Сода"]
