"""
Read-only access to the corporate logistics dictionaries.

The tables live in the external ``dict`` schema (`back/dicts.sql`): countries,
transport types, vehicle types, their mapping and organizations. This module
issues SELECTs only — nothing here (or anywhere else in the project) writes to
that schema, and no migration or seeder creates its tables.

A missing table is not an error: the read model returns a short built-in list
and marks the payload ``source="fallback"`` so the UI can warn that the values
are demo data. An existing but EMPTY table is a real empty dictionary and
returns no rows — the fallback must not hide that state.
"""

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .constants import (
    COUNTRY_MARKET_ORDER,
    FALLBACK_COUNTRIES,
    FALLBACK_ORG_PREFIXES,
    FALLBACK_TRANSPORT_TYPES,
    LOGISTICS_SOURCE_SCHEMA,
)
from .models import LogisticsHazardClass
from .schemas import (
    LogisticsCountryRowRead,
    LogisticsDictionaryRead,
    LogisticsHazardClassRowRead,
    LogisticsOrgRowRead,
    LogisticsTransportRowRead,
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _qualified(schema: str, table: str) -> str:
    """Quote a schema-qualified table name (the schema is a trusted constant)."""
    if _IDENTIFIER.fullmatch(schema) is None or _IDENTIFIER.fullmatch(table) is None:
        message = f"Недопустимое имя таблицы: {schema}.{table}"
        raise ValueError(message)
    return f'"{schema}"."{table}"'


# Public alias for the other logistics read models (the rates table lives in the
# app schema but reads the same external dictionaries).
qualified_table = _qualified


async def _relation_exists(session: AsyncSession, table: str) -> bool:
    """Whether ``dict.<table>`` exists; safe when the schema itself is absent."""
    name = f"{LOGISTICS_SOURCE_SCHEMA}.{table}"
    return bool(await session.scalar(text("SELECT to_regclass(:name) IS NOT NULL").bindparams(name=name)))


def _payload[T](rows: list[T], *, external: bool) -> LogisticsDictionaryRead[T]:
    source: Literal["external", "fallback"] = "external" if external else "fallback"
    return LogisticsDictionaryRead[T](source=source, rows=rows)


# ─── Countries ─────────────────────────────────────────────────────────────


def _fallback_countries() -> list[LogisticsCountryRowRead]:
    return [
        LogisticsCountryRowRead(
            id=f"fallback-country:{index}",
            shortname=name,
            market=country_market(alpha2, ts, sng),
        )
        for index, (name, alpha2, ts, sng) in enumerate(FALLBACK_COUNTRIES, start=1)
    ]


def country_market(alpha2: str | None, ts: bool | None, sng: bool | None) -> Literal["РФ", "БЗ", "ДЗ"]:
    """
    Return the computed «Рынок» of a country.

    RU is the home market («РФ»); a CIS or customs-union member («ts»/«sng») is
    «БЗ»; every other country is «ДЗ». The rule lives in ONE place so the
    external rows and the fallback list cannot disagree.
    """
    if (alpha2 or "").strip().upper() == "RU":
        return "РФ"
    if ts or sng:
        return "БЗ"
    return "ДЗ"


def _sorted_countries(
    rows: list[LogisticsCountryRowRead],
) -> list[LogisticsCountryRowRead]:
    """Group by market (РФ, БЗ, ДЗ), countries alphabetically inside each group."""
    return sorted(rows, key=lambda row: (COUNTRY_MARKET_ORDER[row.market], row.shortname))


async def read_countries(session: AsyncSession) -> LogisticsDictionaryRead[LogisticsCountryRowRead]:
    """Return country short names and their computed market from ``dict.countries``."""
    if not await _relation_exists(session, "countries"):
        return _payload(_sorted_countries(_fallback_countries()), external=False)
    rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                f"SELECT id, shortname, alpha2, ts, sng "  # noqa: S608 — identifier is whitelisted
                f"FROM {_qualified(LOGISTICS_SOURCE_SCHEMA, 'countries')} "
                "WHERE deleted_at IS NULL ORDER BY shortname"
            )
        )
    ).all()
    return _payload(
        _sorted_countries(
            [
                LogisticsCountryRowRead(
                    id=str(row.id),
                    shortname=row.shortname,
                    market=country_market(row.alpha2, row.ts, row.sng),
                )
                for row in rows
            ]
        ),
        external=True,
    )


# ─── Transport types × vehicle types ───────────────────────────────────────


def _fallback_transport_rows() -> list[LogisticsTransportRowRead]:
    rows: list[LogisticsTransportRowRead] = []
    for type_index, (transport, vehicles) in enumerate(FALLBACK_TRANSPORT_TYPES, start=1):
        type_id = f"fallback-transport:{type_index}"
        for vehicle_index, vehicle in enumerate(vehicles, start=1):
            rows.append(
                LogisticsTransportRowRead(
                    id=f"{type_id}:{vehicle_index}",
                    transportTypeId=type_id,
                    transportType=transport,
                    vehicleTypeId=None,
                    vehicleType=vehicle,
                )
            )
    return rows


async def read_transport_types(
    session: AsyncSession,
) -> LogisticsDictionaryRead[LogisticsTransportRowRead]:
    """
    Transport types with their vehicle types, sorted type-first then vehicle.

    A transport type without (valid) mappings keeps one row with an empty
    ``vehicleType``; a mapping to a deleted vehicle is ignored.
    """
    tables = ("transport_types", "vehicle_types", "transport_types_vehicle_types")
    for table in tables:
        if not await _relation_exists(session, table):
            return _payload(_fallback_transport_rows(), external=False)
    transport = _qualified(LOGISTICS_SOURCE_SCHEMA, "transport_types")
    vehicle = _qualified(LOGISTICS_SOURCE_SCHEMA, "vehicle_types")
    mapping = _qualified(LOGISTICS_SOURCE_SCHEMA, "transport_types_vehicle_types")
    # The valid pairs are resolved in a subquery, then LEFT JOINed to the
    # transport types: a type whose mappings ALL point at deleted vehicles still
    # keeps one row with an empty vehicle cell, exactly like a type with no
    # mappings at all. Only `deleted_at IS NULL` rows of every table are used.
    rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                f"SELECT t.id AS transport_id, t.shortname AS transport_type, "  # noqa: S608 — identifiers are whitelisted
                f"v.id AS vehicle_id, v.name AS vehicle_type "
                f"FROM {transport} t "
                "LEFT JOIN ("
                f"SELECT m.transport_type_id, m.vehicle_type_id FROM {mapping} m "
                f"JOIN {vehicle} v ON v.id = m.vehicle_type_id AND v.deleted_at IS NULL "
                "WHERE m.deleted_at IS NULL"
                ") mv ON mv.transport_type_id = t.id "
                f"LEFT JOIN {vehicle} v ON v.id = mv.vehicle_type_id "
                "WHERE t.deleted_at IS NULL "
                "ORDER BY t.shortname, v.name"
            )
        )
    ).all()
    return _payload(
        [
            LogisticsTransportRowRead(
                id=str(row.vehicle_id) if row.vehicle_id is not None else f"{row.transport_id}:",
                transportTypeId=str(row.transport_id),
                transportType=row.transport_type,
                vehicleTypeId=str(row.vehicle_id) if row.vehicle_id is not None else None,
                vehicleType=row.vehicle_type or "",
            )
            for row in rows
        ],
        external=True,
    )


# ─── Organizations ─────────────────────────────────────────────────────────


def _fallback_orgs() -> list[LogisticsOrgRowRead]:
    return [
        LogisticsOrgRowRead(id=f"fallback-org:{index}", prefixRu=prefix)
        for index, prefix in enumerate(FALLBACK_ORG_PREFIXES, start=1)
    ]


async def read_orgs(session: AsyncSession) -> LogisticsDictionaryRead[LogisticsOrgRowRead]:
    """All Russian organization prefixes from ``dict.orgs``."""
    if not await _relation_exists(session, "orgs"):
        return _payload(_fallback_orgs(), external=False)
    rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                f"SELECT id, prefix_ru FROM {_qualified(LOGISTICS_SOURCE_SCHEMA, 'orgs')} "  # noqa: S608 — identifier is whitelisted
                "WHERE deleted_at IS NULL ORDER BY prefix_ru"
            )
        )
    ).all()
    return _payload(
        [LogisticsOrgRowRead(id=str(row.id), prefixRu=row.prefix_ru) for row in rows],
        external=True,
    )


async def read_org_labels(session: AsyncSession) -> dict[UUID, tuple[str, object]]:
    """
    Asset labels INCLUDING soft-deleted rows, for the user's asset grants.

    A grant survives the asset's soft deletion (the rates do the same), so the
    label of such a row carries the `deleted_at` marker and the caller renders
    «УДАЛЕНО: …». Missing table → an empty map (the grants then render blank).
    """
    if not await _relation_exists(session, "orgs"):
        return {}
    rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                f"SELECT id, prefix_ru, deleted_at FROM {_qualified(LOGISTICS_SOURCE_SCHEMA, 'orgs')}"  # noqa: S608 — identifier is whitelisted
            )
        )
    ).all()
    return {row.id: (row.prefix_ru, row.deleted_at) for row in rows}


# ─── Hazard classes (application-owned reference data) ─────────────────────


async def read_hazard_classes(session: AsyncSession) -> list[LogisticsHazardClassRowRead]:
    """Return the nine «Класс опасности» values, ordered 1..9."""
    rows = (await session.exec(select(LogisticsHazardClass).order_by(col(LogisticsHazardClass.value)))).all()
    return [LogisticsHazardClassRowRead(value=row.value, name=row.name) for row in rows]
