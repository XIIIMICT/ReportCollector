"""
Create a DEVELOPMENT fixture of the corporate `dict` schema.

⚠️ This is NOT part of the application contract. The corporate dictionaries
belong to another system: no migration creates them and no seeder writes them.
This script exists only because a developer's database usually has no `dict`
data, and «Ставки» cannot run without it (the main table references those
tables by FOREIGN KEY — migration 0035 needs them, and every rates request
resolves its labels there).

What it does, in ONE transaction, against `LOGISTICS_DICT_SCHEMA` (default
`dict`):

- creates the five tables the application reads, ONLY IF THEY DO NOT EXIST
  (`countries`, `orgs`, `transport_types`, `vehicle_types`,
  `transport_types_vehicle_types`);
- inserts a small demo set with FIXED UUIDs and `ON CONFLICT DO NOTHING`.

It never drops, alters or deletes anything. Two consequences worth knowing:

- an existing corporate table is left untouched; its column set must already
  match what the readers select (`shortname`, `alpha2`, `ts`, `sng`,
  `deleted_at` for countries, `prefix_ru` for orgs, and so on);
- if the fixture created a table and the real dumps are loaded later, the dump
  needs the fixture table dropped first (the operator's call — this script will
  not do it).

Usage: `just seed-dev-dict` (which passes `--yes`).
"""

# ruff: noqa: S608 — every interpolated identifier is a validated schema name.

import argparse
import asyncio
import re

from sqlalchemy import text

from src.config import get_settings
from src.database import async_engine

# The fixture's identifiers are fixed, so a re-run is a no-op and a demo row a
# developer referenced from a rate stays valid.
_COUNTRY_IDS = {
    "ru": "00000000-0000-0000-0000-000000000201",
    "by": "00000000-0000-0000-0000-000000000202",
    "kz": "00000000-0000-0000-0000-000000000205",
    "de": "00000000-0000-0000-0000-000000000203",
    "cn": "00000000-0000-0000-0000-000000000206",
    "tr": "00000000-0000-0000-0000-000000000207",
}
_ORG_IDS = {
    "bsk": "00000000-0000-0000-0000-000000000101",
    "soda": "00000000-0000-0000-0000-000000000102",
    "azot": "00000000-0000-0000-0000-000000000104",
}
_TRANSPORT_IDS = {
    "auto": "00000000-0000-0000-0000-000000000301",
    "rail": "00000000-0000-0000-0000-000000000302",
    "sea": "00000000-0000-0000-0000-000000000303",
}
_VEHICLE_IDS = {
    "tent": "00000000-0000-0000-0000-000000000401",
    "reefer": "00000000-0000-0000-0000-000000000402",
    "gondola": "00000000-0000-0000-0000-000000000403",
}
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

_COUNTRIES_DDL = """
    CREATE TABLE IF NOT EXISTS "{schema}".countries (
        id uuid PRIMARY KEY,
        shortname text NOT NULL,
        alpha2 text NULL,
        ts boolean NULL,
        sng boolean NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NULL,
        deleted_at timestamptz NULL
    )
"""
_ORGS_DDL = """
    CREATE TABLE IF NOT EXISTS "{schema}".orgs (
        id uuid PRIMARY KEY,
        branch text NOT NULL,
        shortname text NOT NULL,
        fullname text NOT NULL,
        inn text NOT NULL,
        prefix_en text NOT NULL,
        prefix_ru text NOT NULL,
        directum_rx_code text NULL,
        org_type text NOT NULL,
        entry_date date NULL,
        exit_date date NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NULL,
        deleted_at timestamptz NULL
    )
"""
_TRANSPORT_TYPES_DDL = """
    CREATE TABLE IF NOT EXISTS "{schema}".transport_types (
        id uuid PRIMARY KEY,
        code text NOT NULL UNIQUE,
        shortname text NOT NULL UNIQUE,
        fullname text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NULL,
        deleted_at timestamptz NULL
    )
"""
_VEHICLE_TYPES_DDL = """
    CREATE TABLE IF NOT EXISTS "{schema}".vehicle_types (
        id uuid PRIMARY KEY,
        "name" text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NULL,
        deleted_at timestamptz NULL
    )
"""
_MAPPINGS_DDL = """
    CREATE TABLE IF NOT EXISTS "{schema}".transport_types_vehicle_types (
        transport_type_id uuid NOT NULL REFERENCES "{schema}".transport_types (id),
        vehicle_type_id uuid NOT NULL REFERENCES "{schema}".vehicle_types (id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NULL,
        deleted_at timestamptz NULL,
        PRIMARY KEY (transport_type_id, vehicle_type_id)
    )
"""


def _ddl(schema: str) -> tuple[str, ...]:
    """Build the five create-if-missing statements, parents first (no FKs on `orgs`)."""
    return (
        _COUNTRIES_DDL.format(schema=schema),
        _ORGS_DDL.format(schema=schema),
        _TRANSPORT_TYPES_DDL.format(schema=schema),
        _VEHICLE_TYPES_DDL.format(schema=schema),
        _MAPPINGS_DDL.format(schema=schema),
    )


def _demo_rows(schema: str) -> tuple[tuple[str, dict[str, object]], ...]:
    """Return the demo inserts, each idempotent on its fixed id."""
    countries = (
        ("ru", "Россия", "RU", False, False),
        ("by", "Беларусь", "BY", False, True),
        ("kz", "Казахстан", "KZ", False, True),
        ("de", "Германия", "DE", False, False),
        ("cn", "Китай", "CN", False, False),
        ("tr", "Турция", "TR", False, False),
    )
    orgs = (
        ("bsk", "БСК", "0200000001"),
        ("soda", "Сода", "0200000002"),
        ("azot", "Азот", "0200000003"),
    )
    transports = (
        ("auto", "auto", "Автомобильный", "Автомобильный транспорт"),
        ("rail", "rail", "Железнодорожный", "Железнодорожный транспорт"),
        ("sea", "sea", "Морской", "Морской транспорт"),
    )
    vehicles = (("tent", "Тент"), ("reefer", "Рефрижератор"), ("gondola", "Полувагон"))
    # «Морской» deliberately has NO mappings: «Ставки» must allow an empty
    # vehicle type exactly there.
    mappings = (("auto", "tent"), ("auto", "reefer"), ("rail", "gondola"))
    statements: list[tuple[str, dict[str, object]]] = []
    country_sql = f"""
        INSERT INTO "{schema}".countries (id, shortname, alpha2, ts, sng)
        VALUES (:id, :shortname, :alpha2, :ts, :sng)
        ON CONFLICT (id) DO NOTHING
    """
    for key, shortname, alpha2, ts, sng in countries:
        statements.append(
            (
                country_sql,
                {
                    "id": _COUNTRY_IDS[key],
                    "shortname": shortname,
                    "alpha2": alpha2,
                    "ts": ts,
                    "sng": sng,
                },
            )
        )
    org_sql = f"""
        INSERT INTO "{schema}".orgs
            (id, branch, shortname, fullname, inn, prefix_en, prefix_ru, org_type)
        VALUES (:id, :branch, :shortname, :fullname, :inn, :prefix_en, :prefix_ru, :org_type)
        ON CONFLICT (id) DO NOTHING
    """
    for key, prefix, inn in orgs:
        statements.append(
            (
                org_sql,
                {
                    "id": _ORG_IDS[key],
                    "branch": "Демонстрационный филиал",
                    "shortname": prefix,
                    "fullname": f"АО «{prefix}»",
                    "inn": inn,
                    "prefix_en": prefix.upper(),
                    "prefix_ru": prefix,
                    "org_type": "ДЗО",
                },
            )
        )
    transport_sql = f"""
        INSERT INTO "{schema}".transport_types (id, code, shortname, fullname)
        VALUES (:id, :code, :shortname, :fullname)
        ON CONFLICT (id) DO NOTHING
    """
    for key, code, shortname, fullname in transports:
        statements.append(
            (
                transport_sql,
                {
                    "id": _TRANSPORT_IDS[key],
                    "code": code,
                    "shortname": shortname,
                    "fullname": fullname,
                },
            )
        )
    vehicle_sql = f"""
        INSERT INTO "{schema}".vehicle_types (id, "name") VALUES (:id, :name)
        ON CONFLICT (id) DO NOTHING
    """
    for key, name in vehicles:
        statements.append((vehicle_sql, {"id": _VEHICLE_IDS[key], "name": name}))
    mapping_sql = f"""
        INSERT INTO "{schema}".transport_types_vehicle_types
            (transport_type_id, vehicle_type_id)
        VALUES (:transport_type_id, :vehicle_type_id)
        ON CONFLICT DO NOTHING
    """
    for transport, vehicle in mappings:
        statements.append(
            (
                mapping_sql,
                {
                    "transport_type_id": _TRANSPORT_IDS[transport],
                    "vehicle_type_id": _VEHICLE_IDS[vehicle],
                },
            )
        )
    return tuple(statements)


async def run(schema: str) -> dict[str, int]:
    """Create the missing fixture tables and insert the demo rows."""
    if _IDENTIFIER.fullmatch(schema) is None:
        message = f"Недопустимое имя схемы: {schema}"
        raise ValueError(message)
    inserted = 0
    async with async_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        for statement in _ddl(schema):
            await conn.execute(text(statement))
        for statement, params in _demo_rows(schema):
            result = await conn.execute(text(statement), params)
            inserted += result.rowcount or 0
    return {"inserted": inserted}


def main() -> None:
    """Parse the confirmation flag and seed the demo dictionaries."""
    parser = argparse.ArgumentParser(description="Демонстрационные справочники «Логистики» для локальной разработки.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтвердить создание демонстрационных таблиц во внешней схеме.",
    )
    arguments = parser.parse_args()
    if not arguments.yes:
        message = (
            "Скрипт создаёт ДЕМОНСТРАЦИОННЫЕ таблицы во внешней схеме справочников "
            "и предназначен только для локальной разработки. Запустите с --yes "
            "(или через `just seed-dev-dict`), если это ваш локальный Postgres."
        )
        raise SystemExit(message)
    schema = get_settings().logistics_dict_schema
    result = asyncio.run(run(schema))
    print(
        f"Демонстрационные справочники в схеме {schema} готовы: "
        f"добавлено строк={result['inserted']}. "
        "Перед загрузкой настоящих дампов эти таблицы нужно удалить."
    )


if __name__ == "__main__":
    main()
