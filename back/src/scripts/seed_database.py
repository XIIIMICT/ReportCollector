"""
Creates the schema from scratch and fills ReportCollector's base data.

First version: all tables are recreated (create_all), projects, project
roles, «Сырьё» dictionaries (assets, delivery bases, raw-material items and
their links), movement periods and a single user — Евтушенко (admin of all
projects) are filled. «Движение» and «Цены» form rows are generated from
the raw-material×asset links: one price row per link and one movement row
per link per period.

Usage:
  uv run --directory back -m src.scripts.seed_database [--reset | --renovation | --data-only]

The renovation plan gets a small deterministic demo set only. Importing real
«РБ ПОФ» workbooks into the plan is done exclusively by the production Excel
import endpoint; there is no seed-time workbook parser.
"""

import argparse
import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.passwords import hash_password
from src.auth.service import sync_project_memberships
from src.config import get_settings
from src.database import async_engine, async_session_factory
from src.logistics.constants import LOGISTICS_PROJECT_KEY, LOGISTICS_PROJECT_NAME
from src.logistics.models import LOGISTICS_PRICE_KIND
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    RawMaterialUserAsset,
    ReportSnapshot,
)
from src.renovation.constants import R_ASSETS, RENOVATION_PROJECT_KEY, RENOVATION_PROJECT_NAME
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id
from src.renovation.service import recompute_plan_rows
from src.scripts.seed_kppk import seed_administrators
from src.scripts.seed_logistics import seed_logistics

# ─── Seed data (first version) ───────────────────────────────────────────

PROJECTS: list[tuple[str, str]] = [
    (RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME),
    (RENOVATION_PROJECT_KEY, RENOVATION_PROJECT_NAME),
    ("kppk", "КППК"),
    (LOGISTICS_PROJECT_KEY, LOGISTICS_PROJECT_NAME),
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


# ─── Isolated e2e fixtures (opt-in; never in production) ───────────────────
# The smoke specs sign in as four reserved local-auth accounts. They are created
# only when RC_SEED_E2E_FIXTURES=1 on an isolated CI/local stand with
# KEYCLOAK_ENABLED=false. The password comes from E2E_TEST_PASSWORD (see the
# root .env.example) — no credential literal belongs in the repository.
RC_SEED_E2E_FIXTURES = "RC_SEED_E2E_FIXTURES"
E2E_CREDENTIAL_ENV = "E2E_TEST_PASSWORD"
E2E_ARCHIVE_CREATOR = "e2e-fixture"


class SeedE2EUser(TypedDict):
    """Reserved local-auth user used by the smoke specs."""

    email: str
    name: str
    role: ProjectRole
    assets: list[str]


# Four distinct people so the collaboration presence assertions see separate
# members: an admin and the three asset workers the raw-materials specs use.
SEED_E2E_USERS: tuple[SeedE2EUser, ...] = (
    {
        "email": "user1@test.local",
        "name": "Евтушенко Сергей Николаевич",
        "role": ProjectRole.ADMIN,
        "assets": [],
    },
    {
        "email": "user2@test.local",
        "name": "Смирнов Иван Викторович",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["СНХЗ"],
    },
    {
        "email": "user3@test.local",
        "name": "Петров Дмитрий Анатольевич",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["СХ", "СНХЗ"],
    },
    {
        "email": "user4@test.local",
        "name": "Лебедева Ольга Ивановна",
        "role": ProjectRole.ASSET_WORKER,
        "assets": ["ВОС"],
    },
)

# Work codes for the four demo plan rows: unique inside the working plan, so
# they are «Корректные» for the work-code facet spec and do not affect import or
# submit validation (see the Gate B decision in docs/TODO.md).
E2E_WORK_CODES: tuple[str, ...] = (
    "RC-E2E-0001",
    "RC-E2E-0002",
    "RC-E2E-0003",
    "RC-E2E-0004",
)


def e2e_fixtures_enabled() -> bool:
    """Explicit opt-in: an isolated stand must ask for these fixtures."""
    return os.environ.get(RC_SEED_E2E_FIXTURES) == "1"


def e2e_test_password() -> str:
    """Fail early with an actionable message instead of seeding empty hashes."""
    password = os.environ.get(E2E_CREDENTIAL_ENV, "")
    if not password:
        msg = f"Set {E2E_CREDENTIAL_ENV} when {RC_SEED_E2E_FIXTURES}=1 (isolated stands only)."
        raise ValueError(msg)
    if len(password) < 8:
        msg = f"{E2E_CREDENTIAL_ENV} must contain at least 8 characters."
        raise ValueError(msg)
    return password


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


async def _seed_e2e_archived_plan(
    session: AsyncSession,
    renovation_project: Project,
    now: datetime,
) -> None:
    """One frozen version so the renovation version picker is deterministic."""
    existing = (
        await session.exec(
            select(RenovationPlan).where(
                RenovationPlan.project_id == renovation_project.id,
                RenovationPlan.state == RPlanState.ARCHIVED,
                RenovationPlan.created_by == E2E_ARCHIVE_CREATOR,
            )
        )
    ).one_or_none()
    if existing is not None:
        return
    frozen_at = now - timedelta(days=1)
    session.add(
        RenovationPlan(
            project_id=renovation_project.id,
            state=RPlanState.ARCHIVED,
            baseline_generation=0,
            created_at=frozen_at,
            created_by=E2E_ARCHIVE_CREATOR,
            updated_at=frozen_at,
            updater_user_id=None,
        )
    )
    await session.flush()


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

    # Renovation working plan + a small deterministic demo row set. The plan is
    # recreated only when no working plan exists yet — for a fresh re-seed,
    # truncate the renovation tables first (see `run --renovation`).
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
        # A small deterministic demo so the grid has content after a fresh
        # seed. The legacy «РБ ПОФ» workbook import was removed: real plan
        # data now arrives ONLY through the production Excel import endpoint.
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
                    work_or_repair_code=E2E_WORK_CODES[position] if e2e_fixtures_enabled() else None,
                    process_position=object_name,
                    bpof_section="Ремонтный бюджет",
                    work_category_id=renovation_reference_by_id[
                        reference_option_id("work_category", "Остановочный капитальный ремонт")
                    ],
                    work_type_id=renovation_reference_by_id[reference_option_id("work_type", "ТР")],
                    cost_allocation_type_id=renovation_reference_by_id[reference_option_id("cost_allocation", "OPEX")],
                    execution_method_id=renovation_reference_by_id[
                        reference_option_id("execution_method", "Собственные силы")
                    ],
                    defect_list_or_bill_of_quantities_number=defect,
                    work_start_date=date(2027, 1, 1),
                    work_end_date=date(2027, 1, 31),
                    work_cost_rub_ex_vat=plan_amount,
                    customer_materials_cost_rub_ex_vat=fact_amount,
                    lotting_activity_direction_code="1.1.1.",
                )
            )
        session.info["renovation_plan_rows"] = 4
        await session.flush()
        # The demo rows carry no calculated fields; materialize them before the
        # plan is usable (the same recompute the maintenance command runs).
        recomputed = await recompute_plan_rows(session, plan_id=working_plan.id)
        session.info["renovation_plan_recomputed"] = recomputed.changed

    if e2e_fixtures_enabled():
        await _seed_e2e_archived_plan(session, renovation_project, now)

    return projects


async def _seed_e2e_users_and_snapshot(
    session: AsyncSession,
    projects: dict[str, Project],
    assets: dict[str, RawMaterialAsset],
    now: datetime,
) -> User:
    """Create the reserved login users and a preliminary movement snapshot."""
    password = e2e_test_password()
    admin_user: User | None = None
    for fixture in SEED_E2E_USERS:
        email = fixture["email"]
        user = (await session.exec(select(User).where(User.email == email))).one_or_none()
        if user is None:
            user = User(
                email=email,
                name=fixture["name"],
                is_deleted=False,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            await session.flush()
        user.password_hash = hash_password(password)

        if fixture["role"] == ProjectRole.ADMIN:
            # The admin works in every project (raw materials, КППК, renovation,
            # logistics) — the same single mechanism as the seed admin.
            await sync_project_memberships(session, user.id, activate_admin=True)
            admin_user = user
            renovation_project = projects[RENOVATION_PROJECT_KEY]
            r_link = (
                await session.exec(
                    select(RUserRole).where(
                        RUserRole.user_id == user.id,
                        RUserRole.project_id == renovation_project.id,
                    )
                )
            ).one_or_none()
            if r_link is None:
                session.add(
                    RUserRole(
                        user_id=user.id,
                        project_id=renovation_project.id,
                        role=RProjectRole.ADMIN,
                        block=None,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        else:
            project = projects[RAW_MATERIALS_PROJECT_KEY]
            link = (
                await session.exec(
                    select(UserRole).where(UserRole.user_id == user.id, UserRole.project_id == project.id)
                )
            ).one_or_none()
            if link is None:
                session.add(
                    UserRole(
                        user_id=user.id,
                        project_id=project.id,
                        role=fixture["role"],
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            for asset_name in fixture["assets"]:
                grant = (
                    await session.exec(
                        select(RawMaterialUserAsset).where(
                            RawMaterialUserAsset.user_id == user.id,
                            RawMaterialUserAsset.asset_id == assets[asset_name].id,
                        )
                    )
                ).one_or_none()
                if grant is None:
                    session.add(RawMaterialUserAsset(user_id=user.id, asset_id=assets[asset_name].id))
    await session.flush()

    if admin_user is None:
        msg = "SEED_E2E_USERS must contain one admin"
        raise ValueError(msg)

    # A PRELIMINARY snapshot on the first movement period makes the raw-materials
    # version picker visible while leaving that month fixable, so the fix-month
    # dialog specs still run on a freshly seeded stand. No snapshot rows are
    # needed: the specs only assert the picker, never open the archived grid.
    first_period = MOVEMENT_PERIODS[0]
    snapshot = (
        await session.exec(select(ReportSnapshot).where(ReportSnapshot.report_month == first_period))
    ).one_or_none()
    if snapshot is None:
        session.add(
            ReportSnapshot(
                report_month=first_period,
                fixed_at=now - timedelta(days=1),
                fixed_by_user_id=admin_user.id,
                fixed_by_email=str(admin_user.email),
                fixed_by_fullname=str(admin_user.name),
                is_final=False,
                previous_snapshot_id=None,
            )
        )
        await session.flush()
    return admin_user


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

    if e2e_fixtures_enabled():
        await _seed_e2e_users_and_snapshot(session, projects, assets, now)

    await seed_logistics(session)
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


async def run(
    reset: bool = False,
    renovation_only: bool = False,
    data_only: bool = False,
) -> None:
    settings = get_settings()
    if data_only and (reset or renovation_only):
        message = "--data-only нельзя совмещать с --reset или --renovation."
        raise ValueError(message)
    if e2e_fixtures_enabled():
        e2e_test_password()
        if settings.keycloak_enabled:
            msg = f"{RC_SEED_E2E_FIXTURES} is for isolated stands: set KEYCLOAK_ENABLED=false."
            raise ValueError(msg)
    async with async_engine.begin() as conn:
        if reset:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{settings.postgres_schema}" CASCADE'))
        if not data_only:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.postgres_schema}"'))
            await conn.run_sync(SQLModel.metadata.create_all)
            # The «Логистика» price-kind enum has no table yet (the main table is
            # the next phase), so `create_all` cannot see it; create it here so a
            # dev reset matches what `just migrate` produces.
            await conn.run_sync(lambda sync_conn: LOGISTICS_PRICE_KIND.create(sync_conn, checkfirst=True))
        if renovation_only and not reset:
            # Re-seed only the renovation data: wipe its tables (not app_users)
            # and let seed_renovation rebuild them.
            tables = ", ".join(f'"{settings.postgres_schema}"."{t}"' for t in RENOVATION_TABLES)
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))

    async with async_session_factory() as session:
        if renovation_only:
            await seed_renovation(session)
            await seed_logistics(session)
            await session.commit()
            plan_rows_count = session.info.get("renovation_plan_rows", 0)
            recomputed = session.info.get("renovation_plan_recomputed", 0)
            print(
                "Сидирование реновации завершено: "
                f"активы реновации={len(R_ASSETS)}, справочники реновации={len(REFERENCE_OPTIONS)}, "
                f"пользователи реновации={len(RENOVATION_USERS)}, "
                f"строк рабочего плана реновации={plan_rows_count}, "
                f"пересчитано расчётных строк={recomputed}, "
                f"e2e-пользователи={len(SEED_E2E_USERS) if e2e_fixtures_enabled() else 0}."
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
            f"пересчитано расчётных строк={recomputed}, "
            f"e2e-пользователи={len(SEED_E2E_USERS) if e2e_fixtures_enabled() else 0}."
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
    asyncio.run(
        run(
            reset=args.reset,
            renovation_only=args.renovation,
            data_only=args.data_only,
        )
    )


if __name__ == "__main__":
    main()
