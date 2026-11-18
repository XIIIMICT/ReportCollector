"""Static reference data of the «Логистика» project (project key and section ids)."""

from src.config import get_settings

# The project row itself is inserted by migration 0032 (production runs
# `just migrate`); the dev seed also lists it in `PROJECTS`.
LOGISTICS_PROJECT_KEY = "logistics"
LOGISTICS_PROJECT_NAME = "Логистика"

# The external schema owned by the corporate logistics database. The
# dictionaries below READ it and never write to it: no migration creates these
# tables, no seeder touches them, and the API exposes no mutations for them. If
# a table is missing, the read model falls back to a short built-in list so the
# UI can still be opened (the response says `source="fallback"`).
#
# Configurable for tests: they point it at their own schema and never touch the
# real `dict`. Production keeps the default.
LOGISTICS_SOURCE_SCHEMA = get_settings().logistics_dict_schema

# Dictionary ids of the workspace tabs (match the frontend). The external
# dictionaries are read-only: the data belongs to the external tables, so they
# have no collaboration room and no undo journal (but every grid exports).
# «Пользователи» are ours and collaborate like the other projects' ones.
LOGISTICS_SECTION_COUNTRIES = "logisticsCountries"
LOGISTICS_SECTION_TRANSPORT_TYPES = "logisticsTransportTypes"
LOGISTICS_SECTION_ORGS = "logisticsOrgs"
LOGISTICS_SECTION_USERS = "logisticsUsers"
LOGISTICS_SECTION_RATES = "logisticsRates"

LOGISTICS_DICTIONARY_SECTIONS = (
    LOGISTICS_SECTION_COUNTRIES,
    LOGISTICS_SECTION_TRANSPORT_TYPES,
    LOGISTICS_SECTION_ORGS,
    LOGISTICS_SECTION_USERS,
    LOGISTICS_SECTION_RATES,
)

# The built-in demo values used ONLY when the external table does not exist.
# An existing but empty table is a real empty dictionary and returns no rows —
# the fallback must never hide that state.
#
# A country is (shortname, alpha2, ts, sng): the last three feed the computed
# «Рынок» exactly like the external columns do (RU → РФ, ts/sng → БЗ, else ДЗ).
FALLBACK_COUNTRIES: tuple[tuple[str, str, bool, bool], ...] = (
    ("Россия", "RU", False, False),
    ("Беларусь", "BY", False, True),
    ("Казахстан", "KZ", True, False),
    ("Германия", "DE", False, False),
)

# Transport type → its vehicle types, in the order the dictionary lists them.
FALLBACK_TRANSPORT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Автомобильный", ("Тентованный", "Рефрижератор", "Изотермический")),
    ("Железнодорожный", ("Полувагон", "Крытый вагон", "Цистерна")),
    ("Морской", ("Контейнеровоз",)),
)

FALLBACK_ORG_PREFIXES: tuple[str, ...] = (
    "БСК",
    "Сода",
    "Метафракс",
)

# Presentation order of the computed country markets.
COUNTRY_MARKET_ORDER: dict[str, int] = {"РФ": 0, "БЗ": 1, "ДЗ": 2}
