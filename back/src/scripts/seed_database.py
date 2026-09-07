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
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypedDict
from uuid import UUID, uuid4

from openpyxl import load_workbook
from sqlalchemy import text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.passwords import hash_password
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
from src.renovation.reference_data import REFERENCE_OPTIONS

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


# Renovation users: central roles span all assets; asset workers follow the
# legacy pattern td-<asset>/opof-<asset>/umto-<asset>@ruschem.ru and see only
# their own asset. The three department prefixes map to the responsibility
# blocks: td → tashpaev, opof → borodiichuk, umto → babaeva.
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
        "email": "tashpaev@ruschem.ru",
        "name": "Ташпаев Алексей Анатольевич",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.TASHPAEV,
        "assets": [],
    },
    {
        "email": "borodiichuk.av@ruschem.ru",
        "name": "Бородийчук Алексей Викторович",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.BORODIICHUK,
        "assets": [],
    },
    {
        "email": "babaeva.ir@ruschem.ru",
        "name": "Бабаева Ирена Рэмовна",
        "role": RProjectRole.BLOCK_OWNER,
        "block": RBlock.BABAEVA,
        "assets": [],
    },
]
# Asset workers: td → tashpaev, opof → borodiichuk, umto → babaeva blocks.
for _asset_code in RENOVATION_ASSET_WORKER_ASSETS:
    RENOVATION_USERS.extend(
        [
            {
                "email": f"td-{_asset_code}@ruschem.ru",
                "name": f"Технический департамент ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.TASHPAEV,
                "assets": [_asset_code],
            },
            {
                "email": f"opof-{_asset_code}@ruschem.ru",
                "name": f"Производственно-финансовый отдел ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.BORODIICHUK,
                "assets": [_asset_code],
            },
            {
                "email": f"umto-{_asset_code}@ruschem.ru",
                "name": f"Управление МТО ({_asset_code.upper()})",
                "role": RProjectRole.ASSET_WORKER,
                "block": RBlock.BABAEVA,
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


# ─── Renovation plan import from the «Пообъектный план» Excel file ─────────
#
# The legacy workbook ("Общее оборудование" sheet) holds ~94k rows × 217
# columns; the first 12 rows are a multi-level header and a column-numbering
# helper row, the data starts at row 13. Excel column N maps 1:1 onto the
# plan-row field col_00N: column 1 is the row position («№ п/п»), column 2 is
# the asset («Предприятие», an r_assets reference), columns 3..217 are
# col_003..col_217. Calculated fields (formulas) are skipped — the application
# recomputes them from the imported raw values.

# Repository-root-relative path of the seed workbook (also the fallback name
# used when the seed is run from anywhere).
RENOVATION_PLAN_XLSX = Path(__file__).resolve().parents[3] / "docs" / "seed" / "renovation_plan_vos_2027.xlsx"

# First data row of the «Общее оборудование» sheet (1-based, after the header).
XLSX_DATA_START_ROW = 13


def _column_types() -> dict[int, str]:
    """Excel column number → cell type (from the form schema, col_00N ↔ column N)."""
    return {int(meta.db_name[4:]): meta.cell_type for meta in field_metas()}


def _column_catalogs() -> dict[int, str]:
    """Excel column number → reference catalog id (reference fields only)."""
    return {int(meta.db_name[4:]): meta.catalog for meta in field_metas() if meta.catalog is not None}


# Fields whose Excel value is a formula (sums, quarterly splits, corrections)
# or a derived identifier — the app recalculates them. Imported as NULL.
# Derived from the form schema: everything with is_calculated=True.
XLSX_SKIP_COLUMNS: frozenset[int] = frozenset(int(meta.db_name[4:]) for meta in field_metas() if meta.is_calculated)


def _normalize_asset_name(name: str) -> str:
    """Compare asset names regardless of the quote style («» vs "")."""
    return name.strip().replace("«", '"').replace("»", '"')


def _parse_decimal(value: object) -> Decimal | None:
    """Coerce a numeric Excel cell to Decimal (handles strings and thousands)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if text == "":
        return None
    # Any Unicode whitespace works as a thousands separator (regular space,
    # NBSP \u00a0, narrow NBSP \u202f — Excel uses different ones); comma → dot.
    text = "".join(text.split()).replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_date(value: object) -> date | None:
    """Coerce an Excel date cell to a datetime.date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
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


def load_renovation_plan_rows(
    asset_by_normalized_name: dict[str, UUID],
    reference_id: dict[tuple[str, str], UUID],
    limit: int | None = None,
) -> list[RenovationPlanRow]:
    """
    Read the «Пообъектный план» workbook into RenovationPlanRow objects.

    Only non-calculated columns are imported; reference columns are resolved
    to renovation_reference_values ids by (catalog, label), and the asset is
    resolved by its normalized name. `limit` caps the number of rows for
    smoke-testing a big file.
    """
    column_types = _column_types()
    column_catalogs = _column_catalogs()
    workbook = load_workbook(RENOVATION_PLAN_XLSX, read_only=True, data_only=False)
    sheet = workbook["Общее оборудование"]
    rows: list[RenovationPlanRow] = []
    unmatched_assets: set[str] = set()
    now = utc_now()
    try:
        for row in sheet.iter_rows(min_row=XLSX_DATA_START_ROW, values_only=True):
            if limit is not None and len(rows) >= limit:
                break
            position_value = row[0] if len(row) > 0 else None
            if position_value is None or str(position_value).strip() == "":
                # A gap row (e.g. a trailing empty line) — stop only if the
                # position is not numeric, otherwise keep going.
                continue
            try:
                position = int(position_value)
            except (TypeError, ValueError) as _:
                continue

            asset_name = _normalize_asset_name(str(row[1])) if len(row) > 1 and row[1] is not None else ""
            asset_id = asset_by_normalized_name.get(asset_name)
            if asset_id is None:
                # Unknown enterprise name — skip the row rather than violate
                # the non-null asset foreign key.
                unmatched_assets.add(asset_name)
                continue

            row_values: dict[str, Any] = {}
            for col_number in range(3, min(len(row), 217) + 1):
                if col_number in XLSX_SKIP_COLUMNS:
                    continue
                value = row[col_number - 1]
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    continue
                field = f"col_{col_number:03d}"
                cell_type = column_types.get(col_number, "text")
                catalog = column_catalogs.get(col_number)
                if catalog is not None:
                    ref = reference_id.get((catalog, str(value).strip()))
                    if ref is not None:
                        row_values[field] = ref
                elif cell_type == "number":
                    parsed = _parse_decimal(value)
                    if parsed is not None:
                        row_values[field] = parsed
                elif cell_type == "date":
                    parsed = _parse_date(value)
                    if parsed is not None:
                        row_values[field] = parsed
                elif cell_type == "boolean":
                    parsed = _parse_bool(value)
                    if parsed is not None:
                        row_values[field] = parsed
                else:
                    parsed = _parse_text(value)
                    if parsed is not None:
                        row_values[field] = parsed

            rows.append(
                RenovationPlanRow(
                    asset_id=asset_id,
                    position=position,
                    baseline_generation=1,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                    updated_by=SEED_ADMIN_EMAIL,
                    **row_values,
                )
            )
    finally:
        workbook.close()
    if unmatched_assets:
        print(f"Внимание: пропущены строки с неизвестными предприятиями: {sorted(unmatched_assets)}")
    return rows


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
        asset = (await session.exec(select(RAsset).where(RAsset.code == code))).one_or_none()
        if asset is None:
            asset = RAsset(code=code, repair_code=repair_code, name=name, created_at=now)
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
    for catalog, value_key, label, sort_order in REFERENCE_OPTIONS:
        option = (
            await session.exec(
                select(RenovationReferenceValue).where(
                    RenovationReferenceValue.catalog == catalog,
                    RenovationReferenceValue.value_key == value_key,
                )
            )
        ).one_or_none()
        if option is None:
            session.add(
                RenovationReferenceValue(
                    catalog=catalog,
                    value_key=value_key,
                    label=label,
                    sort_order=sort_order,
                )
            )
    await session.flush()

    # Renovation working plan + rows (the legacy «Пообъектный план», or a small
    # deterministic demo when the workbook is absent). The plan is recreated
    # only when no working plan exists yet — for a fresh re-import, truncate
    # the renovation tables first (see `run --renovation`).
    renovation_reference_by_key: dict[str, RenovationReferenceValue] = {}
    for value in (await session.exec(select(RenovationReferenceValue))).all():
        renovation_reference_by_key[value.value_key] = value
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
            updated_by=SEED_ADMIN_EMAIL,
        )
        session.add(working_plan)
        await session.flush()
        # Resolve the «Предприятие» name (straight quotes) to an r_assets id,
        # and reference labels to renovation_reference_values ids.
        asset_by_normalized_name = {
            _normalize_asset_name(name): r_assets_by_code[code].id for code, _, name in R_ASSETS
        }
        reference_id: dict[tuple[str, str], UUID] = {}
        for value in (await session.exec(select(RenovationReferenceValue))).all():
            reference_id[(value.catalog, value.label)] = value.id

        if RENOVATION_PLAN_XLSX.exists():
            # Import the real legacy plan (~94k rows) so the grid exercises
            # lazy-loading and heavy data handling right after seeding.
            plan_rows = load_renovation_plan_rows(asset_by_normalized_name, reference_id)
            for row in plan_rows:
                row.plan_id = working_plan.id
            # One flush for the whole batch — SQLAlchemy uses insertmanyvalues
            # (fast multi-row INSERT) instead of 94k round-trips.
            session.add_all(plan_rows)
            await session.flush()
            session.info["renovation_plan_rows"] = len(plan_rows)
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
                        updated_by=SEED_ADMIN_EMAIL,
                        col_011=object_name,
                        col_016="Ремонтный бюджет",
                        col_017=renovation_reference_by_key["work_category_002"].id,
                        col_018=renovation_reference_by_key["work_type_002"].id,
                        col_019=renovation_reference_by_key["cost_allocation_002"].id,
                        col_020=renovation_reference_by_key["execution_method_001"].id,
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

    return projects


async def seed(session: AsyncSession) -> None:
    """Fill the base data (idempotent: existing rows are not recreated)."""
    now = utc_now()
    seed_password = get_settings().auth_seed_admin_password

    projects = await seed_renovation(session)

    # «Активы» dictionary
    assets: dict[str, RawMaterialAsset] = {}
    for name in ASSET_NAMES:
        asset = RawMaterialAsset(name=name, created_at=now, updated_at=now)
        session.add(asset)
        assets[name] = asset
    await session.flush()

    # «Базисы поставки» dictionary
    bases: dict[str, DeliveryBasis] = {}
    for name in DELIVERY_BASIS_NAMES:
        basis = DeliveryBasis(name=name, created_at=now, updated_at=now)
        session.add(basis)
        bases[name] = basis
    await session.flush()

    # «Сырьевые позиции» dictionary + asset-raw-material links
    materials: dict[str, RawMaterial] = {}
    for name in RAW_MATERIAL_NAMES:
        material = RawMaterial(name=name, created_at=now, updated_at=now)
        session.add(material)
        materials[name] = material
    await session.flush()

    arms: list[AssetRawMaterial] = []
    for material_name, asset_names in RAW_MATERIAL_ASSETS:
        for asset_name in asset_names:
            arm = AssetRawMaterial(
                id=uuid4(),
                asset_id=assets[asset_name].id,
                raw_material_id=materials[material_name].id,
                created_at=now,
                updated_at=now,
            )
            session.add(arm)
            arms.append(arm)
    await session.flush()

    # Movement periods + «Движение» rows for each link in each period.
    for report_month in MOVEMENT_PERIODS:
        session.add(MovementPeriod(report_month=report_month, created_at=now))
    await session.flush()
    for report_month in MOVEMENT_PERIODS:
        for arm_index, arm in enumerate(arms):
            session.add(
                MaterialMovement(
                    id=uuid4(),
                    asset_raw_material_id=arm.id,
                    report_month=report_month,
                    created_at=now,
                    updated_at=now,
                    **_movement_values(arm_index),
                )
            )
    await session.flush()

    # «Цены» rows: one per asset-raw-material link.
    for arm_index, arm in enumerate(arms):
        session.add(
            RawMaterialPrice(
                id=uuid4(),
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

    for project in projects.values():
        link = (
            await session.exec(select(UserRole).where(UserRole.user_id == user.id, UserRole.project_id == project.id))
        ).one_or_none()
        if link is None:
            session.add(
                UserRole(
                    user_id=user.id,
                    project_id=project.id,
                    role=ProjectRole.ADMIN,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
    await session.flush()

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
# plan. `app_users` and `projects` are left untouched — real users already work
# in the system. TRUNCATE ... CASCADE also clears anything that references them
# (r_user_assets/r_user_roles → app_users are CASCADE anyway).
RENOVATION_TABLES: tuple[str, ...] = (
    "renovation_plan_rows",
    "renovation_plans",
    "renovation_reference_values",
    "r_user_assets",
    "r_user_roles",
    "r_assets",
)


async def run(reset: bool = False, renovation_only: bool = False, data_only: bool = False) -> None:
    settings = get_settings()
    if data_only and (reset or renovation_only):
        message = "--data-only нельзя совмещать с --reset или --renovation."
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

    async with async_session_factory() as session:
        if renovation_only:
            await seed_renovation(session)
            await session.commit()
            plan_rows_count = session.info.get("renovation_plan_rows", 0)
            print(
                "Сидирование реновации завершено: "
                f"активы реновации={len(R_ASSETS)}, справочники реновации={len(REFERENCE_OPTIONS)}, "
                f"пользователи реновации={len(RENOVATION_USERS)}, "
                f"строк рабочего плана реновации={plan_rows_count}."
            )
            return

        await seed(session)
        arms_count = sum(len(assets) for _, assets in RAW_MATERIAL_ASSETS)
        plan_rows_count = session.info.get("renovation_plan_rows", 4)
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
            f"строк рабочего плана реновации={plan_rows_count}."
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
    args = parser.parse_args()
    asyncio.run(run(reset=args.reset, renovation_only=args.renovation, data_only=args.data_only))


if __name__ == "__main__":
    main()
