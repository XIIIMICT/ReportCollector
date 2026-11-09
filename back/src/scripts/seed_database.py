"""
Creates the schema from scratch and fills ReportCollector's base data.

First version: all tables are recreated (create_all), projects, project
roles, «Сырьё» dictionaries (assets, delivery bases, raw-material items and
their links), movement periods and a single user — Евтушенко (admin of all
projects) are filled. «Движение» and «Цены» form rows are generated from
the raw-material×asset links: one price row per link and one movement row
per link per period.

Usage:
  uv run --directory back -m src.scripts.seed_database [--reset]
"""

import argparse
import asyncio
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict
from uuid import UUID, uuid4

from openpyxl import load_workbook
from pyxlsb import open_workbook
from sqlalchemy import func, text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.passwords import hash_password
from src.auth.service import sync_project_memberships
from src.config import get_settings
from src.database import async_engine, async_session_factory
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    RawMaterialUserAsset,
)
from src.renovation.constants import R_ASSETS, RENOVATION_PROJECT_KEY, RENOVATION_PROJECT_NAME
from src.renovation.form_schema import field_metas
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id
from src.renovation.renovation_id import DIRECTION_CODE_FIELD, normalize_direction_code
from src.renovation.service import recompute_plan_rows
from src.scripts.seed_kppk import seed_administrators

# ─── Seed data (first version) ───────────────────────────────────────────

PROJECTS: list[tuple[str, str]] = [
    (RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME),
    (RENOVATION_PROJECT_KEY, RENOVATION_PROJECT_NAME),
    ("kppk", "КППК"),
]

ASSET_NAMES: list[str] = [
    "БСЗ",
    "БСК (Каустик)",
    "БСК (Сода)",
    "ВОС",
    "ДГОК",
    "Кучуксульфат",
    "Метадинеа (Губаха)",
    "Метадинеа (ОЗ)",
    "Метафракс",
    "РМУ",
    "СЗ",
    "СНХЗ",
    "СХ",
    "ТИ",
]

DELIVERY_BASIS_NAMES: list[str] = ["CPT", "FCA", "FOB"]

RAW_MATERIAL_NAMES: list[str] = [
    "Алкилат",
    "Аммиак безводный сжиженный",
    "Аммиачная вода",
    "Апатит",
    "Бутилен - бутадиеновая фракция",
    "Бутиловый спирт",
    "Гидросульфид натрия",
    "Диметиламин",
    "Диоктилфталат",
    "Известняк для производства кальцинированной соды",
    "Изобутилен",
    "Ильменитовый концентрат",
    "Калий едкий жидкий",
    "Калий едкий чешуированный",
    "Калий хлористый",
    "Карбамид",
    "Карбамид фас",
    "Кокс металлургический",
    "Концентрат Сильвин",
    "Меламин",
    "Метанол",
    "Натр едкий (100%)",
    "Натр едкий твёрдый",
    "Пропилен",
    "Сера гранулированная",
    "Сера жидкая",
    "Сера комовая",
    "Серная кислота",
    "Стирол",
    "Уголь марки АО (антрацит)",
    "Уголь марки Б",
    "Уголь марки Д",
    "Фенол",
    "Фракция изопентановая ГПНС",
    "Фракция изопентановая Сибур",
    "Фракция нормального пентана",
    "Хлористый натрий едкий карьерный",
    "Этилен",
]

# Raw-material → assets links (raw-material name, asset names).
RAW_MATERIAL_ASSETS: list[tuple[str, list[str]]] = [
    ("Алкилат", ["СНХЗ"]),
    ("Аммиак безводный сжиженный", ["БСК (Каустик)", "ВОС", "Метафракс", "СНХЗ", "ТИ"]),
    ("Аммиачная вода", ["БСЗ", "БСК (Сода)", "СЗ"]),
    ("Апатит", ["РМУ"]),
    ("Бутилен - бутадиеновая фракция", ["СНХЗ"]),
    ("Бутиловый спирт", ["ВОС", "СНХЗ"]),
    ("Гидросульфид натрия", ["БСЗ", "БСК (Сода)", "СЗ"]),
    ("Диметиламин", ["ВОС", "СНХЗ"]),
    ("Диоктилфталат", ["БСК (Каустик)"]),
    ("Известняк для производства кальцинированной соды", ["БСК (Сода)", "СЗ"]),
    ("Изобутилен", ["СНХЗ"]),
    ("Ильменитовый концентрат", ["ТИ"]),
    ("Калий едкий жидкий", ["ВОС", "СНХЗ"]),
    ("Калий едкий чешуированный", ["ВОС"]),
    ("Калий хлористый", ["СХ"]),
    ("Карбамид", ["Метадинеа (Губаха)", "Метадинеа (ОЗ)"]),
    ("Карбамид фас", ["Метадинеа (Губаха)", "Метадинеа (ОЗ)"]),
    ("Кокс металлургический", ["БСЗ", "БСК (Сода)"]),
    ("Концентрат Сильвин", ["РМУ"]),
    ("Меламин", ["Метадинеа (ОЗ)"]),
    ("Метанол", ["ВОС", "Метадинеа (ОЗ)", "СНХЗ"]),
    ("Натр едкий (100%)", ["ВОС", "Метадинеа (Губаха)", "Метадинеа (ОЗ)", "Метафракс", "РМУ", "СНХЗ"]),
    ("Натр едкий твёрдый", ["ДГОК"]),
    ("Пропилен", ["ВОС"]),
    ("Сера гранулированная", ["ТИ"]),
    ("Сера жидкая", ["ВОС"]),
    ("Сера комовая", ["ДГОК"]),
    ("Серная кислота", ["ВОС", "Метадинеа (ОЗ)", "Метафракс", "РМУ", "СНХЗ"]),
    ("Стирол", ["СНХЗ"]),
    ("Уголь марки АО (антрацит)", ["БСЗ", "БСК (Сода)", "СЗ"]),
    ("Уголь марки Б", ["ДГОК"]),
    ("Уголь марки Д", ["Кучуксульфат"]),
    ("Фенол", ["СНХЗ"]),
    ("Фракция изопентановая ГПНС", ["СНХЗ"]),
    ("Фракция изопентановая Сибур", ["СНХЗ"]),
    ("Фракция нормального пентана", ["СНХЗ"]),
    ("Хлористый натрий едкий карьерный", ["БСЗ"]),
    ("Этилен", ["БСК (Каустик)"]),
]

# Movement periods (report months of the «Движение» form).
MOVEMENT_PERIODS: list[date] = [date(2026, 8, 1), date(2026, 9, 1)]

# Default delivery basis for price rows (first from the dictionary).
DEFAULT_DELIVERY_BASIS = DELIVERY_BASIS_NAMES[0]

# The only user of the first version — Евтушенко, admin of all projects,
# active in all projects right away.
SEED_ADMIN_EMAIL = "evtushenko.sn@ruschem.ru"
SEED_ADMIN_NAME = "Евтушенко Сергей Николаевич"


class SeedExtraUser(TypedDict):
    """Extra collaboration user (see SEED_EXTRA_USERS)."""

    email: str
    name: str
    role: ProjectRole
    assets: list[str]


# Extra collaboration users:
#   kuznetsov.an — second admin (all projects);
#   smirnov.iv — asset worker, access to СНХЗ only;
#   petrov.da — asset worker, access to СХ and СНХЗ;
#   lebedeva.ol — asset worker, access to ВОС only («На форме» scope).
SEED_EXTRA_USERS: list[SeedExtraUser] = [
    {
        "email": "kuznetsov.an@ruschem.ru",
        "name": "Кузнецов Алексей Николаевич",
        "role": ProjectRole.ADMIN,
        "assets": [],
    },
    {
        "email": "smirnov.iv@ruschem.ru",
        "name": "Смирнов Иван Викторович",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["СНХЗ"],
    },
    {
        "email": "petrov.da@ruschem.ru",
        "name": "Петров Дмитрий Анатольевич",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["СХ", "СНХЗ"],
    },
    {
        "email": "lebedeva.ol@ruschem.ru",
        "name": "Лебедева Ольга Ивановна",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["ВОС"],
    },
]


class SeedRenovationUser(TypedDict):
    """Renovation-project user (role/block/asset scope from the legacy demo)."""

    email: str
    name: str
    role: RProjectRole
    # Responsibility block; required for block_owner/asset_worker.
    block: RBlock | None
    # Asset codes from R_ASSETS; empty means "all assets" (central roles).
    assets: list[str]


# Renovation users. Central roles span all assets; asset workers follow the
# legacy pattern <directorate>-<asset>@ruschem.ru and see only their own asset.
# The demo accounts below are placeholders: block owners/asset workers are named
# by their directorate (tech / ksir / mto) until real employees are provisioned.
RENOVATION_ASSET_WORKER_ASSETS = ["vos", "snhz", "bsk"]
RENOVATION_USERS: list[SeedRenovationUser] = [
    {
        # The platform admin (SEED_ADMIN) — renovation admin with all assets.
        "email": SEED_ADMIN_EMAIL,
        "name": SEED_ADMIN_NAME,
        "role": RProjectRole.ADMIN,
        "block": None,
        "assets": [],
    },
    {
        "email": "vasilenko@ruschem.ru",
        "name": "Василенко Екатерина Игоревна",
        "role": RProjectRole.ADMIN,
        "block": None,
        "assets": [],
    },
    {
        "email": "rogov@ruschem.ru",
        "name": "Рогов Максим Николаевич",
        "role": RProjectRole.APPROVER,
        "block": None,
        "assets": [],
    },
    {
        "email": "tech@ruschem.ru",
        "name": "Техническая дирекция",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.TECH,
        "assets": [],
    },
    {
        "email": "ksir@ruschem.ru",
        "name": "Дирекция КСиР",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.KSIR,
        "assets": [],
    },
    {
        "email": "mto@ruschem.ru",
        "name": "Дирекция МТО",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.MTO,
        "assets": [],
    },
]
# Asset workers: one per directorate and asset (<directorate>-<asset>).
for _asset_code in RENOVATION_ASSET_WORKER_ASSETS:
    RENOVATION_USERS.extend(
        [
            {
                "email": f"tech-{_asset_code}@ruschem.ru",
                "name": f"Техническая дирекция ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.TECH,
                "assets": [_asset_code],
            },
            {
                "email": f"ksir-{_asset_code}@ruschem.ru",
                "name": f"Дирекция КСиР ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.KSIR,
                "assets": [_asset_code],
            },
            {
                "email": f"mto-{_asset_code}@ruschem.ru",
                "name": f"Дирекция МТО ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.MTO,
                "assets": [_asset_code],
            },
        ]
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def _movement_values(index: int) -> dict[str, Any]:
    """Realistic «Движение» row values (deterministic by index)."""
    return {
        "demand_plan_tons": 850.0 + (index % 9) * 125.5,
        "consumption_fact_tons": 830.0 + (index % 7) * 120.0,
        "delivery_plan_tons": 790.0 + (index % 8) * 110.0,
        "delivery_fact_tons": 810.0 + (index % 6) * 130.0,
        "balance_start_tons": 300.0 + (index % 5) * 55.5,
        "balance_on_site_tons": 290.0 + (index % 4) * 60.0,
        "balance_in_transit_tons": 120.0 + (index % 3) * 25.0,
        "balance_end_tons": 310.0 + (index % 6) * 50.0,
        "deviation_reason": "Плановая потребность производства",
    }


def _price_business_plan_tons(index: int) -> float:
    """Compute the business plan of a «Цены» row (deterministic by index)."""
    return 800.0 + (index % 9) * 125.5


# ─── Renovation plan import from the «РБ ПОФ» Excel files ─────────────────
#
# Each enterprise ships its own «РБ ПОФ_2027_*» workbook under
# docs/seed/Планы/; the data lives on a sheet whose name starts with «РБ»
# (the other sheets — «СВОД», «СПРАВОЧНИК», «ПРАВИЛА», «Номенклатура МТР» —
# are auxiliary). Row 1 is the header, row 2 is a column-numbering helper, and
# the data starts at row 3. Column order and names vary per file, so columns are
# matched by their (normalized) header text against the form labels
# (renovation-form.json), NOT by position.
#
# The enterprise is resolved from the «Наименование ДО» code (an r_assets
# code) when present, else from the normalized «Предприятие» name; the
# master dictionaries are never re-seeded — existing asset ids are reused.

# Repository-root-relative folder with the plan workbooks.
RENOVATION_PLAN_DIR = Path(__file__).resolve().parents[3] / "docs" / "seed" / "Планы"
# Stop reading a sheet after this many consecutive blank rows (the sheets declare
# a huge formatted extent well past the data).
_BLANK_ROW_STOP = 200
# Fallback single workbook of the older seed (kept so a checkout without the
# per-enterprise files still imports something).
RENOVATION_PLAN_XLSX = Path(__file__).resolve().parents[3] / "docs" / "seed" / "renovation_plan_vos_2027.xlsx"

# Header text normalisation: unify case, whitespace, line breaks and the two
# quote styles. This is NOT a synonym dictionary — it only erases formatting.
_QUOTE_TRANSLATION = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"'})
# A closed set of source typos we correct (documented, not a synonym system).
_HEADER_TYPOS = {
    "катеория": "категория",
    "техническоо": "технического",
    "технолоическая": "технологическая",
    "едениц": "единиц",
}


def _normalize_asset_name(name: str) -> str:
    """Compare asset names regardless of the quote style («» vs "")."""
    return name.strip().translate(_QUOTE_TRANSLATION)


def _normalize_header(text: object) -> str:
    """Lowercase, collapse whitespace and strip quotes/line breaks of a header."""
    if text is None:
        return ""
    value = str(text).translate(_QUOTE_TRANSLATION)
    value = value.replace("\u00a0", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    value = " ".join(value.split()).strip().lower()
    for wrong, right in _HEADER_TYPOS.items():
        value = value.replace(wrong, right)
    return value


@lru_cache(maxsize=1)
def _header_to_field() -> dict[str, str]:
    """Return normalized form label → plan-row field name (col_00N)."""
    mapping: dict[str, str] = {}
    for meta in field_metas():
        mapping.setdefault(_normalize_header(meta.label), meta.db_name)
    return mapping


# A few source headers label a real form field with different wording (not a
# synonym engine — a closed correction list, verified against the form).
_SOURCE_HEADER_ALIASES: dict[str, str] = {
    _normalize_header("Отнесение затрат"): "col_019",
    _normalize_header("Способ выполнения работ"): "col_020",
    _normalize_header("Инвентарный № (РЕГ.№)"): "col_012",
    _normalize_header("Исполнитель работ"): "col_021",
    # The «РБ ПОФ» workbooks name the direction code (col_189) «Лот» — the value
    # that forms the third segment of the work id (e.g. `1.3.1`).
    _normalize_header("Лот"): "col_189",
}
# Headers matched by prefix (the defect-statement column carries a long
# parenthetical that varies by file; only the leading phrase is stable).
_SOURCE_HEADER_PREFIXES: tuple[tuple[str, str], ...] = (
    (_normalize_header("Дефектная ведомость/ВОР"), "col_023"),
    (_normalize_header("ДВ/ВОР"), "col_023"),
)

# One-off correction of reference VALUES in this seed drop (case variants and
# Cyrillic look-alike typos). NOT a lasting synonym layer — the next export is
# expected to be clean. Keyed by catalog, matched on the casefolded value.
_CATALOG_VALUE_TYPOS: dict[str, dict[str, str]] = {
    "cost_allocation": {"орех": "OPEX", "сарех": "CAPEX"},
    "work_type": {
        "тр": "ТР",
        "кр": "КР",
        "то": "ТО",
        "ср": "СР",
        "диагностическое обследование": "Диагностические обследования",
        "техническое осведетельствование": "Тех. освидетельствование",
    },
    "work_category": {"тоир": "ТОиР", "ториир": "ТОиР"},
    "execution_method": {
        "собственными силами": "Собственные силы",
        "хозспособом": "Хозяйственный способ",
        "хозспособ": "Хозяйственный способ",
        "хоз. способ": "Хозяйственный способ",
    },
}
# Prefix marking a reference option the seed invented because the source value
# was absent from the catalog; the user reviews and cleans these later.
_INVENTED_OPTION_PREFIX = "@"


def _is_integral_number(text: str) -> bool:
    """Whether a string is a whole number (optionally written as a float)."""
    try:
        return float(text) % 1 == 0
    except TypeError, ValueError:
        return False


class _ReferenceResolver:
    """Resolve a source value to a renovation_reference_values id, creating options."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._by_label: dict[tuple[str, str], UUID] = {}
        self._by_fold: dict[tuple[str, str], UUID] = {}
        self._created = 0
        self.created_labels: list[tuple[str, str]] = []

    async def load(self) -> None:
        """Index existing options by exact and casefolded (catalog, label)."""
        for value in (await self._session.exec(select(RenovationReferenceValue))).all():
            self._by_label[(value.catalog.value, value.label)] = value.id
            self._by_fold.setdefault((value.catalog.value, value.label.casefold()), value.id)

    @property
    def created_count(self) -> int:
        """How many invented (@-prefixed) options were added."""
        return self._created

    def resolve(self, catalog: str, raw_value: object) -> UUID | None:
        """Return the option id for a source value (creating an @option if new)."""
        text = " ".join(str(raw_value).replace("\u00a0", " ").split())
        if text == "":
            return None
        # Criticality arrives as a float serial (3.0 → 3).
        if catalog == "criticality" and _is_integral_number(text):
            text = str(int(float(text)))
        typo = _CATALOG_VALUE_TYPOS.get(catalog, {}).get(text.casefold())
        if typo is not None and (catalog, typo) in self._by_label:
            return self._by_label[(catalog, typo)]
        existing = self._by_label.get((catalog, text)) or self._by_fold.get((catalog, text.casefold()))
        if existing is None:
            # An option we already invented for this value (marked with @).
            existing = self._by_fold.get((catalog, f"{_INVENTED_OPTION_PREFIX}{text}".casefold()))
        if existing is not None:
            return existing
        # Unknown value: invent a marked option so it can be reviewed later.
        label = f"{_INVENTED_OPTION_PREFIX}{text}"
        self._created += 1
        self.created_labels.append((catalog, label))
        option = RenovationReferenceValue(
            catalog=catalog,
            value_key=f"{catalog}_{uuid4().hex[:8]}",
            label=label,
            sort_order=1000 + self._created,
        )
        self._session.add(option)
        self._by_label[(catalog, label)] = option.id
        self._by_fold[(catalog, label.casefold())] = option.id
        return option.id


@lru_cache(maxsize=1)
def _field_types() -> dict[str, str]:
    """Return plan-row field name → cell type."""
    return {meta.db_name: meta.cell_type for meta in field_metas()}


@lru_cache(maxsize=1)
def _field_catalogs() -> dict[str, str]:
    """
    Return plan-row field name → reference catalog id (reference fields only).

    `col_002` («Предприятие») also carries the `assets` catalog, but it is the
    asset reference handled separately — never a renovation_reference_values one.
    """
    return {
        meta.db_name: meta.catalog for meta in field_metas() if meta.catalog is not None and meta.db_name != "col_002"
    }


# Calculated fields (formulas / the derived id) are never imported — the app
# recomputes them from the raw values.
_SKIP_FIELDS: frozenset[str] = frozenset(meta.db_name for meta in field_metas() if meta.is_calculated)

# Structural / auxiliary columns that have no form field (plan tracking, lot
# codes, month helpers, actual-cost columns). Matched normalized; anything else
# unmatched is reported so it can be triaged.
_IGNORED_HEADERS: frozenset[str] = frozenset(
    _normalize_header(text)
    for text in (
        "Год",
        "Наименование ДО",
        "Код работы",
        "Код ДВ",
        "Номер ОР",
        "№ п/п",
        "Номер",
        "Цех-Исполнитель",
        "Статья",
        "ОКР",
        "Доведение до лимита",
        "Доведение до лимитов",
        "Месяц начала",
        "Месяц начала работ",
        "Месяц_начала работ",
        "Месяц_начало",
        "Месяц окончания",
        "Месяц окончания работ",
        "Месяц_окончания работ",
        "Месяц_окончание",
        "Месяц освоения",
        "Месяц освоения затрат",
        "ГОД",
        "ГОД начало",
        "ГОД окончание",
        "Продолжительность",
        "Продолжительность, дн",
        "Плановый месяц выполнения работ",
        "Фактический месяц выполнения работ",
        "Прогнозный месяц выполнения работ",
        "Причина неисполнения запланированных работ",
        "Фактическая стоимость работ итого, руб. без НДС",
        "Отклонение, руб. без НДС",
        "Причина отклонения стоимости",
        "Вид работ_дубль",
        "Вид работ (дубль)",
        "Вид_работ дубль",
        "ПОРОТЦ",
        "ЦОРОАСУ",
        "ЦЭС",
        "вид работы (код)",
        "4-й сегмент кода",
    )
)


def _parse_decimal(value: object) -> Decimal | None:
    """Coerce a numeric Excel cell to Decimal (handles strings and thousands)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = "".join(str(value).split()).replace(",", ".")
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_EXCEL_EPOCH = date(1899, 12, 30)


def _parse_date(value: object) -> date | None:
    """Coerce an Excel date cell to a date (serial number or ISO string)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and value > 20000:
        return _EXCEL_EPOCH + timedelta(days=float(value))
    text = str(value).strip()
    if text == "":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_bool(value: object) -> bool | None:
    """Coerce a Да/Нет cell to a boolean."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("да", "true", "1", "истина"):
        return True
    if text in ("нет", "false", "0", "ложь"):
        return False
    return None


def _parse_text(value: object) -> str | None:
    """Coerce a text cell to a trimmed string (empty → None)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iter_sheet_rows(path: Path, sheet: str) -> Iterator[list[object]]:
    """Yield the raw rows of a workbook sheet (StreamingBackend-agnostic)."""
    if path.suffix.lower() == ".xlsb":
        with open_workbook(str(path)) as workbook, workbook.get_sheet(sheet) as worksheet:
            for row in worksheet.rows():
                yield [cell.v for cell in row]
    else:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for row in workbook[sheet].iter_rows(values_only=True):
                yield list(row)
        finally:
            workbook.close()


def _data_sheets(path: Path) -> list[str]:
    """Names of the plan sheets of a workbook (the «РБ…» ones)."""
    if path.suffix.lower() == ".xlsb":
        with open_workbook(str(path)) as workbook:
            sheets = list(workbook.sheets)
    else:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = list(workbook.sheetnames)
        finally:
            workbook.close()
    return [sheet for sheet in sheets if sheet.strip().upper().startswith("РБ")]


@dataclass
class PlanImportStats:
    """Diagnostics collected while iterating the «РБ ПОФ» workbooks."""

    skipped_enterprises: list[str] = field(default_factory=list)
    unmatched_headers: set[str] = field(default_factory=set)


def iter_renovation_plan_rows(
    asset_by_code: dict[str, UUID],
    asset_by_normalized_name: dict[str, UUID],
    references: _ReferenceResolver,
    stats: PlanImportStats,
    *,
    directory: Path = RENOVATION_PLAN_DIR,
    limit: int | None = None,
) -> Iterator[RenovationPlanRow]:
    """
    Yield RenovationPlanRow objects from every «РБ ПОФ_2027_*» workbook.

    A generator, not a list: the real plan is ~0.3M rows and materializing them
    all at once OOM-killed the seed on a small production container (exit 137).

    Columns are resolved by their header text: the «Наименование ДО» code (an
    asset code) picks the asset and every other header is matched against
    the form labels. The plan position is a gap-free global sequence over the
    imported rows (each source file restarts its own «№ п/п»). Calculated fields
    and the structural columns are dropped; reference labels are resolved to
    renovation_reference_values ids. Data starts at the third row of each sheet.
    """
    header_to_field = _header_to_field()
    field_types = _field_types()
    field_catalogs = _field_catalogs()
    files = sorted(
        path
        for path in directory.glob("*")
        if path.suffix.lower() in (".xlsb", ".xlsx") and not path.name.startswith("~$")
    )
    now = utc_now()
    position = 0
    emitted = 0
    for path in files:
        for sheet in _data_sheets(path):
            row_iter = _iter_sheet_rows(path, sheet)
            try:
                header = next(row_iter)
            except StopIteration:
                continue
            # Header text → field name (or None for structural columns).
            columns: list[str | None] = []
            for cell in header:
                normalized = _normalize_header(cell)
                if normalized == "":
                    columns.append(None)
                    continue
                if normalized in _IGNORED_HEADERS:
                    columns.append("__position__" if normalized == "№ п/п" else None)
                    continue
                field = header_to_field.get(normalized) or _SOURCE_HEADER_ALIASES.get(normalized)
                if field is None:
                    field = next(
                        (target for prefix, target in _SOURCE_HEADER_PREFIXES if normalized.startswith(prefix)),
                        None,
                    )
                if field is None:
                    stats.unmatched_headers.add(str(cell).strip())
                    columns.append(None)
                else:
                    columns.append(field)
            code_index = next(
                (i for i, c in enumerate(header) if _normalize_header(c) == _normalize_header("Наименование ДО")),
                None,
            )
            enterprise_index = next(
                (i for i, c in enumerate(header) if _normalize_header(c) == _normalize_header("Предприятие")),
                None,
            )
            # Skip the numbering helper row (row 2); data starts at row 3.
            next(row_iter, None)
            # Sheets declare a huge extent (some report >1M rows of stray
            # formatting); the real data is contiguous, so stop after a long run
            # of blank rows instead of iterating to the declared bottom.
            blank_run = 0
            for raw_row in row_iter:
                if all(cell is None or str(cell).strip() == "" for cell in raw_row):
                    blank_run += 1
                    if blank_run >= _BLANK_ROW_STOP:
                        break
                    continue
                blank_run = 0
                asset_id = _resolve_asset(
                    raw_row, code_index, enterprise_index, asset_by_code, asset_by_normalized_name
                )
                if asset_id is None:
                    stats.skipped_enterprises.append(sheet)
                    continue
                # The source «№ п/п» restarts in every file (and is blank on some
                # rows), so it cannot be the plan position — assign a global,
                # gap-free sequence over the imported rows instead.
                position += 1
                values: dict[str, Any] = {}
                for index, field in enumerate(columns):
                    if field is None or field == "__position__" or index >= len(raw_row):
                        continue
                    if field in _SKIP_FIELDS:
                        continue
                    cell = raw_row[index]
                    if cell is None or (isinstance(cell, str) and cell.strip() == ""):
                        continue
                    catalog = field_catalogs.get(field)
                    if catalog is not None:
                        reference = references.resolve(catalog, cell)
                        if reference is not None:
                            values[field] = reference
                        continue
                    cell_type = field_types.get(field, "text")
                    parsed: object
                    if cell_type == "number":
                        parsed = _parse_decimal(cell)
                    elif cell_type == "date":
                        parsed = _parse_date(cell)
                    elif cell_type == "boolean":
                        parsed = _parse_bool(cell)
                    else:
                        parsed = _parse_text(cell)
                        if parsed is not None and field == DIRECTION_CODE_FIELD:
                            # The «Лот» code always ends with a dot.
                            parsed = normalize_direction_code(parsed)
                    if parsed is not None:
                        values[field] = parsed
                yield RenovationPlanRow(
                    asset_id=asset_id,
                    position=position,
                    baseline_generation=1,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                    updater_user_id=None,
                    **values,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return


async def _flush_plan_batch(session: AsyncSession, batch: list[RenovationPlanRow]) -> None:
    """
    Flush one batch of plan rows, then expunge it to bound memory.

    The «@» reference options invented while reading the batch are still pending
    here. `RenovationPlanRow` and `RenovationReferenceValue` are linked only by a
    bare `ForeignKey` (no ORM relationship), and SQLAlchemy's unit of work does
    not derive insert order from that — it emits the plan rows first, so Postgres
    rejects the FK (`renovation_plan_rows_col_018_fkey`). Flush the pending
    options explicitly before adding the rows (the pre-batching code did the same
    once, after building the whole row list).
    """
    await session.flush()
    session.add_all(batch)
    await session.flush()
    for item in batch:
        session.expunge(item)
    batch.clear()


async def insert_renovation_plan_rows(
    session: AsyncSession,
    plan_id: UUID,
    rows: Iterable[RenovationPlanRow],
    *,
    batch_size: int = 5000,
) -> int:
    """
    Insert plan rows in batches, expunging each batch to bound memory.

    The seed must not hold ~0.3M ORM rows (and SQLAlchemy's identity map) at
    once — that is what OOM-killed it (exit 137). Each batch is flushed and
    expunged, so peak memory stays at `batch_size` rows.
    """
    count = 0
    batch: list[RenovationPlanRow] = []
    for row in rows:
        row.plan_id = plan_id
        batch.append(row)
        if len(batch) >= batch_size:
            await _flush_plan_batch(session, batch)
        count += 1
    if batch:
        await _flush_plan_batch(session, batch)
    return count


def _resolve_asset(
    raw_row: list[object],
    code_index: int | None,
    enterprise_index: int | None,
    asset_by_code: dict[str, UUID],
    asset_by_normalized_name: dict[str, UUID],
) -> UUID | None:
    """Resolve a row's enterprise to an r_assets id (by code, then by name)."""
    if code_index is not None and code_index < len(raw_row) and raw_row[code_index] is not None:
        code = str(raw_row[code_index]).strip().upper()
        if code in asset_by_code:
            return asset_by_code[code]
    if enterprise_index is not None and enterprise_index < len(raw_row) and raw_row[enterprise_index] is not None:
        name = _normalize_asset_name(str(raw_row[enterprise_index]))
        if name in asset_by_normalized_name:
            return asset_by_normalized_name[name]
    return None


async def seed_renovation(session: AsyncSession) -> dict[str, Project]:
    """
    Seed the renovation-project data only (assets, users, catalogs, plan).

    Returns the projects keyed by their key (for reuse by the full seed).
    Does not commit — the caller owns the transaction.
    """
    now = utc_now()
    seed_password = get_settings().auth_seed_admin_password

    # Projects
    projects: dict[str, Project] = {}
    for key, name in PROJECTS:
        project = (await session.exec(select(Project).where(Project.key == key))).one_or_none()
        if project is None:
            project = Project(key=key, name=name, created_at=now)
            session.add(project)
            await session.flush()
        projects[key] = project
    await session.flush()

    # Renovation assets (legal entities): static master data from the legacy form.
    r_assets_by_code: dict[str, RAsset] = {}
    for code, repair_code, name in R_ASSETS:
        asset = (await session.exec(select(RAsset).where(RAsset.code == repair_code))).one_or_none()
        if asset is None:
            asset = RAsset(code=repair_code, name=name, created_at=now)
            session.add(asset)
            await session.flush()
        r_assets_by_code[code] = asset
    await session.flush()

    # Renovation users: role/block link in r_user_roles + asset grants in
    # r_user_assets (central roles get every asset, workers only their own).
    renovation_project = projects[RENOVATION_PROJECT_KEY]
    for ren_user in RENOVATION_USERS:
        email = str(ren_user["email"])
        ren_user_row = (await session.exec(select(User).where(User.email == email))).one_or_none()
        if ren_user_row is None:
            ren_user_row = User(
                email=email,
                name=str(ren_user["name"]),
                is_deleted=False,
                created_at=now,
                updated_at=now,
            )
            session.add(ren_user_row)
            await session.flush()
        if seed_password:
            ren_user_row.password_hash = hash_password(seed_password)

        r_link = (
            await session.exec(
                select(RUserRole).where(
                    RUserRole.user_id == ren_user_row.id, RUserRole.project_id == renovation_project.id
                )
            )
        ).one_or_none()
        if r_link is None:
            session.add(
                RUserRole(
                    user_id=ren_user_row.id,
                    project_id=renovation_project.id,
                    role=ren_user["role"],
                    block=ren_user["block"],
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.flush()

        # Cross-project memberships come from the same single source as the
        # runtime user dictionary (create_renovation_user → sync_project_memberships):
        # a renovation-only user is linked to the renovation project only (raw
        # materials/kppk are skipped when an RUserRole exists), while the seed
        # admin (activate_admin) is made present and admin in every project.
        await sync_project_memberships(
            session,
            ren_user_row.id,
            activate_admin=(email == SEED_ADMIN_EMAIL),
        )

        # Block owners and approvers have implicit access to all assets
        # in their scope — no per-asset grants needed.
        ren_asset_codes = list(ren_user["assets"])
        if not ren_asset_codes and ren_user["role"] not in (
            RProjectRole.BLOCK_OWNER,
            RProjectRole.APPROVER,
        ):
            ren_asset_codes = [code for code, _, _ in R_ASSETS]
        for code in ren_asset_codes:
            grant = (
                await session.exec(
                    select(RUserAsset).where(
                        RUserAsset.user_id == ren_user_row.id, RUserAsset.asset_id == r_assets_by_code[code].id
                    )
                )
            ).one_or_none()
            if grant is None:
                session.add(
                    RUserAsset(
                        user_id=ren_user_row.id,
                        project_id=renovation_project.id,
                        asset_id=r_assets_by_code[code].id,
                        created_at=now,
                    )
                )
    await session.flush()

    # Renovation reference catalogs (static options from renovation-form.json).
    # The id comes from (catalog, label), so a re-seed recognises its own rows;
    # a database seeded before this change keeps its existing row (matched by
    # label) instead of gaining a second copy.
    for catalog, label in REFERENCE_OPTIONS:
        existing = (
            await session.exec(
                select(RenovationReferenceValue).where(
                    RenovationReferenceValue.catalog == catalog,
                    func.lower(RenovationReferenceValue.label) == label.lower(),
                )
            )
        ).one_or_none()
        if existing is None:
            session.add(
                RenovationReferenceValue(
                    id=reference_option_id(catalog, label),
                    catalog=catalog,
                    label=label,
                )
            )
    await session.flush()

    # Renovation working plan + rows (the legacy «Пообъектный план», or a small
    # deterministic demo when the workbook is absent). The plan is recreated
    # only when no working plan exists yet — for a fresh re-import, truncate
    # the renovation tables first (see `run --renovation`).
    renovation_reference_by_id: dict[UUID, UUID] = {}
    for value in (await session.exec(select(RenovationReferenceValue))).all():
        renovation_reference_by_id[value.id] = value.id
    working_plan = (
        await session.exec(
            select(RenovationPlan).where(
                RenovationPlan.project_id == renovation_project.id,
                RenovationPlan.state == RPlanState.WORKING,
            )
        )
    ).one_or_none()
    if working_plan is None:
        working_plan = RenovationPlan(
            project_id=renovation_project.id,
            state=RPlanState.WORKING,
            baseline_generation=1,
            created_at=now,
            created_by=SEED_ADMIN_EMAIL,
            updated_at=now,
            updater_user_id=None,
        )
        session.add(working_plan)
        await session.flush()
        # Resolve the «Предприятие» name (straight quotes) to an r_assets id,
        # and reference labels to renovation_reference_values ids. The plan
        # workbooks name the enterprise by its code (the «Наименование ДО» cell)
        # or by its name, so both lookup tables are prepared.
        asset_by_normalized_name = {
            _normalize_asset_name(name): r_assets_by_code[code].id for code, _, name in R_ASSETS
        }
        asset_by_code = {code.upper(): r_assets_by_code[code].id for code in r_assets_by_code}
        references = _ReferenceResolver(session)
        await references.load()

        plan_dir = RENOVATION_PLAN_DIR
        if plan_dir.is_dir():
            # Import every «РБ ПОФ» workbook (~0.3M rows) so the grid exercises
            # lazy-loading and heavy data handling right after seeding. Batched,
            # so the whole plan is never held in memory (exit 137 otherwise).
            stats = PlanImportStats()
            rows_count = await insert_renovation_plan_rows(
                session,
                working_plan.id,
                iter_renovation_plan_rows(asset_by_code, asset_by_normalized_name, references, stats),
            )
            session.info["renovation_plan_rows"] = rows_count
            session.info["renovation_plan_created_options"] = references.created_count
            if stats.skipped_enterprises:
                print(f"Внимание: строки без предприятия пропущены: {sorted(set(stats.skipped_enterprises))}")
            if stats.unmatched_headers:
                print(f"Внимание: незаполненные заголовки (пропущены): {sorted(stats.unmatched_headers)}")
            if references.created_count:
                print(f"Внимание: добавлено {references.created_count} справочных значений с маркером «@».")
        elif RENOVATION_PLAN_XLSX.exists():
            # The legacy single-workbook format is no longer imported (it is
            # superseded by the per-enterprise «РБ ПОФ» files).
            print("Внимание: «РБ ПОФ»-файлы не найдены, используется демонстрационный план.")
            session.info["renovation_plan_rows"] = 0
        else:
            # Fallback: a small deterministic demo so the grid still has
            # content when the workbook is absent.
            for position, (asset_code, object_name, defect, plan_amount, fact_amount) in enumerate(
                [
                    ("bsk", "Насос НК 65/35-70", "1382467", Decimal(10), Decimal(20)),
                    ("vos", "Резервуар Р-2", "2247000", Decimal(5), Decimal(7)),
                    ("snhz", "Компрессор К-103", "3100550", Decimal(8), Decimal(12)),
                    ("sz", "Задвижка З-14", "4050200", Decimal(15), Decimal(18)),
                ]
            ):
                session.add(
                    RenovationPlanRow(
                        plan_id=working_plan.id,
                        asset_id=r_assets_by_code[asset_code].id,
                        position=position,
                        baseline_generation=1,
                        revision=0,
                        created_at=now,
                        updated_at=now,
                        updater_user_id=None,
                        col_011=object_name,
                        col_016="Ремонтный бюджет",
                        col_017=renovation_reference_by_id[
                            reference_option_id("work_category", "Остановочный капитальный ремонт")
                        ],
                        col_018=renovation_reference_by_id[reference_option_id("work_type", "ТР")],
                        col_019=renovation_reference_by_id[reference_option_id("cost_allocation", "OPEX")],
                        col_020=renovation_reference_by_id[reference_option_id("execution_method", "Собственные силы")],
                        col_023=defect,
                        col_024=date(2027, 1, 1),
                        col_025=date(2027, 1, 31),
                        col_028=plan_amount,
                        col_029=fact_amount,
                        col_189="1.1.1.",
                    )
                )
            session.info["renovation_plan_rows"] = 4
        await session.flush()
        # The importer skips calculated fields; materialize them before the plan
        # is usable (same recompute the maintenance command runs).
        recomputed = await recompute_plan_rows(session, plan_id=working_plan.id)
        session.info["renovation_plan_recomputed"] = recomputed.changed

    return projects


async def seed_renovation_plan(session: AsyncSession) -> None:
    """
    Reimport only the working renovation plan from the «РБ ПОФ» workbooks.

    Used by `--renovation-plan`: the asset dictionary, the reference catalogs and
    the users are kept as they are (their ids must survive); only the plan rows
    are rebuilt. Missing reference values are added as marked («@») options.
    """
    now = utc_now()
    project = (await session.exec(select(Project).where(Project.key == RENOVATION_PROJECT_KEY))).one_or_none()
    if project is None:
        message = "Проект «Реновация» не найден — сначала выполните полное сидирование."
        raise ValueError(message)
    if not RENOVATION_PLAN_DIR.is_dir():
        message = f"Каталог планов не найден: {RENOVATION_PLAN_DIR}."
        raise ValueError(message)

    assets = (await session.exec(select(RAsset))).all()
    if not assets:
        message = "Справочник активов пуст — сначала выполните полное сидирование."
        raise ValueError(message)
    asset_by_code: dict[str, UUID] = {}
    asset_by_normalized_name: dict[str, UUID] = {}
    for asset in assets:
        asset_by_code[asset.code.upper()] = asset.id
        asset_by_normalized_name[_normalize_asset_name(asset.name)] = asset.id

    working_plan = RenovationPlan(
        project_id=project.id,
        state=RPlanState.WORKING,
        baseline_generation=1,
        created_at=now,
        created_by=SEED_ADMIN_EMAIL,
        updated_at=now,
        updater_user_id=None,
    )
    session.add(working_plan)
    await session.flush()

    references = _ReferenceResolver(session)
    await references.load()
    stats = PlanImportStats()
    rows_count = await insert_renovation_plan_rows(
        session,
        working_plan.id,
        iter_renovation_plan_rows(asset_by_code, asset_by_normalized_name, references, stats),
    )
    # The importer skips calculated fields, so materialize them here (the same
    # recompute the maintenance command runs) before the plan is usable.
    recomputed = await recompute_plan_rows(session, plan_id=working_plan.id)
    session.info["renovation_plan_recomputed"] = recomputed.changed
    session.info["renovation_plan_rows"] = rows_count
    session.info["renovation_plan_created_options"] = references.created_count
    if stats.skipped_enterprises:
        print(f"Внимание: строки без предприятия пропущены: {sorted(set(stats.skipped_enterprises))}")
    if stats.unmatched_headers:
        print(f"Внимание: незаполненные заголовки (пропущены): {sorted(stats.unmatched_headers)}")
    if references.created_count:
        print(f"Внимание: добавлено {references.created_count} справочных значений с маркером «@».")


async def seed(session: AsyncSession) -> None:
    """Fill the base data (idempotent: existing rows are not recreated)."""
    now = utc_now()
    seed_password = get_settings().auth_seed_admin_password

    projects = await seed_renovation(session)

    # The «Сырьё» dictionaries are idempotent like the renovation side: an
    # existing row (by its natural key) is reused, never recreated, so re-running
    # the seed against a schema that already holds data doesn't hit the UNIQUE
    # constraints. Rows are iterated in the declared order so the deterministic
    # values below (movement balance, prices) keep the same per-link assignment
    # whether the rows pre-existed or were just created.
    assets = {a.name: a for a in (await session.exec(select(RawMaterialAsset))).all()}
    for name in ASSET_NAMES:
        if name not in assets:
            asset = RawMaterialAsset(name=name, created_at=now, updated_at=now)
            session.add(asset)
            await session.flush()
            assets[name] = asset

    bases = {b.name: b for b in (await session.exec(select(DeliveryBasis))).all()}
    for name in DELIVERY_BASIS_NAMES:
        if name not in bases:
            basis = DeliveryBasis(name=name, created_at=now, updated_at=now)
            session.add(basis)
            await session.flush()
            bases[name] = basis

    materials = {m.name: m for m in (await session.exec(select(RawMaterial))).all()}
    for name in RAW_MATERIAL_NAMES:
        if name not in materials:
            material = RawMaterial(name=name, created_at=now, updated_at=now)
            session.add(material)
            await session.flush()
            materials[name] = material

    existing_arms: dict[tuple[UUID, UUID], AssetRawMaterial] = {
        (arm.asset_id, arm.raw_material_id): arm for arm in (await session.exec(select(AssetRawMaterial))).all()
    }
    arms: list[AssetRawMaterial] = []
    for material_name, asset_names in RAW_MATERIAL_ASSETS:
        for asset_name in asset_names:
            asset_id = assets[asset_name].id
            material_id = materials[material_name].id
            arm = existing_arms.get((asset_id, material_id))
            if arm is None:
                arm = AssetRawMaterial(
                    asset_id=asset_id,
                    raw_material_id=material_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(arm)
                await session.flush()
            arms.append(arm)

    # Movement periods + «Движение» rows for each link in each period.
    existing_periods = {period.report_month for period in (await session.exec(select(MovementPeriod))).all()}
    for report_month in MOVEMENT_PERIODS:
        if report_month not in existing_periods:
            session.add(MovementPeriod(report_month=report_month))
            await session.flush()
    existing_movements = {
        (mov.asset_raw_material_id, mov.report_month) for mov in (await session.exec(select(MaterialMovement))).all()
    }
    for report_month in MOVEMENT_PERIODS:
        for arm_index, arm in enumerate(arms):
            if (arm.id, report_month) in existing_movements:
                continue
            session.add(
                MaterialMovement(
                    asset_raw_material_id=arm.id,
                    report_month=report_month,
                    created_at=now,
                    updated_at=now,
                    **_movement_values(arm_index),
                )
            )
    await session.flush()

    # «Цены» rows: one per asset-raw-material link.
    existing_price_arms = {
        price.asset_raw_material_id for price in (await session.exec(select(RawMaterialPrice))).all()
    }
    for arm_index, arm in enumerate(arms):
        if arm.id in existing_price_arms:
            continue
        session.add(
            RawMaterialPrice(
                asset_raw_material_id=arm.id,
                delivery_basis_id=bases[DEFAULT_DELIVERY_BASIS].id,
                business_plan_quantity_tons=_price_business_plan_tons(arm_index),
                storage_standard_tons=950.0 + (arm_index % 10) * 40.0,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()

    # User — Евтушенко: admin of all projects, active everywhere.
    # The local-login password comes from AUTH_SEED_ADMIN_PASSWORD (if set).
    user = (await session.exec(select(User).where(User.email == SEED_ADMIN_EMAIL))).one_or_none()
    if user is None:
        user = User(
            email=SEED_ADMIN_EMAIL,
            name=SEED_ADMIN_NAME,
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
    if seed_password:
        user.password_hash = hash_password(seed_password)
    # The seed admin is present and admin in every project. Single mechanism:
    # sync_project_memberships(activate_admin=True) — the same function the
    # renovation seed already uses, so the admin's cross-project links are never
    # hand-written in two places. (Idempotent when the renovation seed ran first.)
    await sync_project_memberships(session, user.id, activate_admin=True)

    # Share the administrator seed with the standalone KPPK seeder.
    await seed_administrators(session)

    # Extra users: a second admin and asset workers.
    for extra in SEED_EXTRA_USERS:
        email = str(extra["email"])
        extra_user = (await session.exec(select(User).where(User.email == email))).one_or_none()
        if extra_user is None:
            extra_user = User(
                email=email,
                name=str(extra["name"]),
                is_deleted=False,
                created_at=now,
                updated_at=now,
            )
            session.add(extra_user)
            await session.flush()
        if seed_password:
            extra_user.password_hash = hash_password(seed_password)

        role = extra["role"]
        if role == ProjectRole.ADMIN:
            target_projects = list(projects.values())
        else:
            target_projects = [projects[RAW_MATERIALS_PROJECT_KEY]]
        for project in target_projects:
            link = (
                await session.exec(
                    select(UserRole).where(UserRole.user_id == extra_user.id, UserRole.project_id == project.id)
                )
            ).one_or_none()
            if link is None:
                session.add(
                    UserRole(
                        user_id=extra_user.id,
                        project_id=project.id,
                        role=role,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        await session.flush()

        for asset_name in extra["assets"]:
            asset = assets[asset_name]
            grant = (
                await session.exec(
                    select(RawMaterialUserAsset).where(
                        RawMaterialUserAsset.user_id == extra_user.id,
                        RawMaterialUserAsset.asset_id == asset.id,
                    )
                )
            ).one_or_none()
            if grant is None:
                session.add(RawMaterialUserAsset(user_id=extra_user.id, asset_id=asset.id))
    await session.flush()

    await session.commit()


# Renovation-only tables, truncated by `--renovation` before re-importing the
# plan. The plan and the reference catalogs are rebuilt; `r_assets`,
# `r_user_roles`, `r_user_assets`, `app_users` and `projects` are LEFT ALONE —
# the enterprise dictionary and the people already exist in the real system and
# their ids must be preserved (the importer resolves them by code).
RENOVATION_TABLES: tuple[str, ...] = (
    "r_plan_rows",
    "r_plans",
    "r_reference_values",
)

# Plan-only tables, truncated by `--renovation-plan`: re-reads the plan while
# keeping the asset dictionary, the reference catalogs, the users and their
# role/asset links untouched.
RENOVATION_PLAN_TABLES: tuple[str, ...] = (
    "r_plan_rows",
    "r_plans",
)


async def run(
    reset: bool = False,
    renovation_only: bool = False,
    data_only: bool = False,
    renovation_plan_only: bool = False,
) -> None:
    settings = get_settings()
    if data_only and (reset or renovation_only or renovation_plan_only):
        message = "--data-only нельзя совмещать с --reset, --renovation или --renovation-plan."
        raise ValueError(message)
    if renovation_only and renovation_plan_only:
        message = "--renovation и --renovation-plan не совмещаются."
        raise ValueError(message)
    async with async_engine.begin() as conn:
        if reset:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{settings.postgres_schema}" CASCADE'))
        if not data_only:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.postgres_schema}"'))
            await conn.run_sync(SQLModel.metadata.create_all)
        if renovation_only and not reset:
            # Re-seed only the renovation data: wipe its tables (not app_users)
            # and let seed_renovation rebuild them.
            tables = ", ".join(f'"{settings.postgres_schema}"."{t}"' for t in RENOVATION_TABLES)
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        elif renovation_plan_only:
            # Only the plan is rebuilt; the dictionaries and users stay.
            tables = ", ".join(f'"{settings.postgres_schema}"."{t}"' for t in RENOVATION_PLAN_TABLES)
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))

    async with async_session_factory() as session:
        if renovation_plan_only:
            await seed_renovation_plan(session)
            await session.commit()
            plan_rows_count = session.info.get("renovation_plan_rows", 0)
            created_options = session.info.get("renovation_plan_created_options", 0)
            recomputed = session.info.get("renovation_plan_recomputed", 0)
            print(
                "Пересидирование плана реновации завершено: "
                f"строк рабочего плана={plan_rows_count}, "
                f"добавлено справочных значений «@»={created_options}, "
                f"пересчитано расчётных строк={recomputed}."
            )
            return
        if renovation_only:
            await seed_renovation(session)
            await session.commit()
            plan_rows_count = session.info.get("renovation_plan_rows", 0)
            recomputed = session.info.get("renovation_plan_recomputed", 0)
            print(
                "Сидирование реновации завершено: "
                f"активы реновации={len(R_ASSETS)}, справочники реновации={len(REFERENCE_OPTIONS)}, "
                f"пользователи реновации={len(RENOVATION_USERS)}, "
                f"строк рабочего плана реновации={plan_rows_count}, "
                f"пересчитано расчётных строк={recomputed}."
            )
            return

        await seed(session)
        arms_count = sum(len(assets) for _, assets in RAW_MATERIAL_ASSETS)
        plan_rows_count = session.info.get("renovation_plan_rows", 4)
        recomputed = session.info.get("renovation_plan_recomputed", 0)
        print(
            f"Сидирование завершено: проектов={len(PROJECTS)}, активов={len(ASSET_NAMES)}, "
            f"базисов={len(DELIVERY_BASIS_NAMES)}, сырьё={len(RAW_MATERIAL_NAMES)}, "
            f"связок актив-сырьё={arms_count}, периодов={len(MOVEMENT_PERIODS)}, "
            f"строк цен={arms_count}, строк движения={arms_count * len(MOVEMENT_PERIODS)}, "
            f"пользователь={SEED_ADMIN_EMAIL} (admin всех проектов), "
            f"доп. пользователи={len(SEED_EXTRA_USERS)} ({', '.join(str(u['email']) for u in SEED_EXTRA_USERS)}), "
            f"пользователи реновации={len(RENOVATION_USERS)} "
            f"({', '.join(str(u['email']) for u in RENOVATION_USERS)}), "
            f"активы реновации={len(R_ASSETS)}, справочники реновации={len(REFERENCE_OPTIONS)}, "
            f"строк рабочего плана реновации={plan_rows_count}, "
            f"пересчитано расчётных строк={recomputed}."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание схемы и сидирование базовых данных ReportCollector.")
    parser.add_argument("--reset", action="store_true", help="Пересоздать схему перед сидированием")
    parser.add_argument("--data-only", action="store_true", help="Только данные: схема должна быть создана миграциями")
    parser.add_argument(
        "--renovation",
        action="store_true",
        help="Перезатереть только таблицы реновации (без drop schema и без app_users) и пересидировать их",
    )
    parser.add_argument(
        "--renovation-plan",
        action="store_true",
        help="Переимпортировать только рабочий план реновации (активы, справочники и пользователи сохраняются)",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            reset=args.reset,
            renovation_only=args.renovation,
            data_only=args.data_only,
            renovation_plan_only=args.renovation_plan,
        )
    )


if __name__ == "__main__":
    main()
