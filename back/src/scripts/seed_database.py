"""
Создаёт схему с нуля и заполняет базовые данные ReportCollector.

Первая версия: все таблицы создаются заново (create_all), заполняются
проекты, роли проектов, справочники проекта «Сырьё» (активы, базисы
поставки, сырьевые позиции и их связки), периоды движения и единственный
пользователь — Евтушенко (администратор всех проектов). Строки форм
«Движение» и «Цены» генерируются из связок сырьё-актив: по строке цен на
каждую связку и по строке движения на каждую связку в каждом периоде.

Использование:
  uv run --directory back -m src.scripts.seed_database [--reset]
"""

import argparse
import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME
from src.auth.models import Project, ProjectRole, User, UserRole
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
)

# ─── Данные сидирования (первая версия) ────────────────────────────

PROJECTS: list[tuple[str, str]] = [
    (RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME),
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

# Связки «сырьё → активы» (название сырья, названия активов).
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

# Периоды движения (отчётные месяцы формы «Движение»).
MOVEMENT_PERIODS: list[date] = [date(2026, 8, 1), date(2026, 9, 1)]

# Базис поставки по умолчанию для строк цен (первый из справочника).
DEFAULT_DELIVERY_BASIS = DELIVERY_BASIS_NAMES[0]

# Единственный пользователь первой версии — Евтушенко, администратор
# всех проектов, активен во всех проектах сразу.
SEED_ADMIN_EMAIL = "evtushenko.sn@ruschem.ru"
SEED_ADMIN_NAME = "Евтушенко Сергей Николаевич"


def utc_now() -> datetime:
    return datetime.now(UTC)


async def seed(session: AsyncSession) -> None:
    """Заполняет базовые данные (идемпотентно: существующие не пересоздаёт)."""
    now = utc_now()

    # Проекты
    projects: dict[str, Project] = {}
    for key, name in PROJECTS:
        project = (await session.exec(select(Project).where(Project.key == key))).one_or_none()
        if project is None:
            project = Project(key=key, name=name, created_at=now)
            session.add(project)
            await session.flush()
        projects[key] = project
    await session.flush()

    # Справочник «Активы»
    assets: dict[str, RawMaterialAsset] = {}
    for name in ASSET_NAMES:
        asset = RawMaterialAsset(name=name, created_at=now, updated_at=now)
        session.add(asset)
        assets[name] = asset
    await session.flush()

    # Справочник «Базисы поставки»
    bases: dict[str, DeliveryBasis] = {}
    for name in DELIVERY_BASIS_NAMES:
        basis = DeliveryBasis(name=name, created_at=now, updated_at=now)
        session.add(basis)
        bases[name] = basis
    await session.flush()

    # Справочник «Сырьевые позиции» + связки актив-сырьё
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

    # Периоды движения + строки «Движения» для каждой связки в каждом периоде.
    for report_month in MOVEMENT_PERIODS:
        session.add(MovementPeriod(report_month=report_month, created_at=now))
    await session.flush()
    for report_month in MOVEMENT_PERIODS:
        for arm in arms:
            session.add(
                MaterialMovement(
                    id=uuid4(),
                    asset_raw_material_id=arm.id,
                    report_month=report_month,
                    created_at=now,
                    updated_at=now,
                )
            )
    await session.flush()

    # Строки «Цен»: по одной на каждую связку актив-сырьё.
    for arm in arms:
        session.add(
            RawMaterialPrice(
                id=uuid4(),
                asset_raw_material_id=arm.id,
                delivery_basis_id=bases[DEFAULT_DELIVERY_BASIS].id,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()

    # Пользователь — Евтушенко: администратор всех проектов, активен везде.
    user = (await session.exec(select(User).where(User.email == SEED_ADMIN_EMAIL))).one_or_none()
    if user is None:
        user = User(
            key=SEED_ADMIN_EMAIL,
            email=SEED_ADMIN_EMAIL,
            name=SEED_ADMIN_NAME,
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()

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
            f"пользователь={SEED_ADMIN_EMAIL} (admin всех проектов)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание схемы и сидирование базовых данных ReportCollector.")
    parser.add_argument("--reset", action="store_true", help="Пересоздать схему перед сидированием")
    args = parser.parse_args()
    asyncio.run(run(reset=args.reset))


if __name__ == "__main__":
    main()
