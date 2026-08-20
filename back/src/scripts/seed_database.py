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
from decimal import Decimal
from typing import Any, TypedDict
from uuid import uuid4

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
        "storage_standard_tons": 950.0 + (index % 10) * 40.0,
        "deviation_reason": "Плановая потребность производства",
    }


def _price_business_plan_tons(index: int) -> float:
    """Compute the business plan of a «Цены» row (deterministic by index)."""
    return 800.0 + (index % 9) * 125.5


async def seed(session: AsyncSession) -> None:
    """Fill the base data (idempotent: existing rows are not recreated)."""
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

        ren_asset_codes = list(ren_user["assets"])
        if not ren_asset_codes:
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

    # Renovation working plan + demo rows (deterministic subset of the legacy
    # plan so the grid has content right after seeding). Idempotent: the plan
    # is created only when no working plan exists yet.
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
        await session.flush()

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


async def run(reset: bool = False) -> None:
    settings = get_settings()
    async with async_engine.begin() as conn:
        if reset:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{settings.postgres_schema}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.postgres_schema}"'))
        await conn.run_sync(SQLModel.metadata.create_all)

    async with async_session_factory() as session:
        await seed(session)
        arms_count = sum(len(assets) for _, assets in RAW_MATERIAL_ASSETS)
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
            f"строк рабочего плана реновации=4."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание схемы и сидирование базовых данных ReportCollector.")
    parser.add_argument("--reset", action="store_true", help="Пересоздать схему перед сидированием")
    args = parser.parse_args()
    asyncio.run(run(reset=args.reset))


if __name__ == "__main__":
    main()
