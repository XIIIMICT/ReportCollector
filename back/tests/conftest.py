"""Shared fixtures for ReportCollector tests."""

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.auth.models import User

# Env vars must be set before the first src.* import, because src.database
# caches settings at module level.
# POSTGRES_HOST defaults to the raw loopback IP, not `localhost`: on Windows
# `localhost` resolves to ::1 first and Docker Desktop swallows that connection
# attempt until the TCP retransmission timeout (~21 s per fresh connection),
# which turns every test that opens pooled connections into a timing flake.
os.environ.setdefault("AUTH_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("AUTH_IDENTITY_TTL", "3600")
os.environ.setdefault("AUTH_SEED_ADMIN_EMAIL", "admin@example.ru")
os.environ.setdefault("AUTH_SEED_ADMIN_PASSWORD", "AdminPassword123")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "strong_password")
os.environ.setdefault("POSTGRES_DB", "dwh")
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_SCHEMA", "rc")
os.environ.setdefault("BACKEND_CORS_ORIGINS", '["http://localhost:5000","http://127.0.0.1:5000"]')
os.environ.setdefault("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:strong_password@127.0.0.1:5432/dwh")
os.environ.setdefault("TEST_SCHEMA", "rc_test")
COLLAB_BACKEND = os.environ.get("TEST_COLLAB_BACKEND", "memory")
if COLLAB_BACKEND not in {"memory", "valkey"}:
    message = "TEST_COLLAB_BACKEND must be memory or valkey"
    raise pytest.UsageError(message)
os.environ["VALKEY_URL"] = os.environ.get("TEST_VALKEY_URL", "") if COLLAB_BACKEND == "valkey" else ""

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]
# Normalize an explicitly provided URL to the loopback IP for the same reason;
# `127.0.0.1` behaves identically to `localhost` on Linux CI.
TEST_DATABASE_URL = TEST_DATABASE_URL.replace("@localhost:", "@127.0.0.1:")
TEST_SCHEMA = os.environ["TEST_SCHEMA"]
BACKEND_DIR = Path(__file__).resolve().parents[1]
if not TEST_SCHEMA.startswith("rc_test") or not TEST_SCHEMA.replace("_", "").isalnum():
    message = "TEST_SCHEMA must be a dedicated rc_test* schema"
    raise pytest.UsageError(message)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    """Test session engine: model metadata registered, schema created per-test."""
    from src.auth import models as _auth_models  # noqa: F401
    from src.collab import models as _collab_models  # noqa: F401
    from src.raw_materials import models as _raw_materials_models  # noqa: F401

    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
        pool_pre_ping=True,
    )
    yield engine
    await engine.dispose()


async def _seed_session(session: AsyncSession) -> None:
    """Minimal «Сырьё» dataset: users, arms, periods, prices, rows."""
    from src.auth.constants import RAW_MATERIALS_PROJECT_KEY, RAW_MATERIALS_PROJECT_NAME
    from src.auth.models import Project, ProjectRole, User, UserRole
    from src.raw_materials.models import (
        AssetRawMaterial,
        DeliveryBasis,
        MaterialMovement,
        MovementPeriod,
        RawMaterial,
        RawMaterialAsset,
        RawMaterialPrice,
    )

    now = datetime.now(UTC)
    project = Project(key=RAW_MATERIALS_PROJECT_KEY, name=RAW_MATERIALS_PROJECT_NAME)
    session.add(project)
    await session.flush()

    admin = User(
        email="admin@example.ru",
        name="Admin User",
        created_at=now,
        updated_at=now,
    )
    session.add(admin)
    await session.flush()
    session.add(
        UserRole(
            user_id=admin.id,
            project_id=project.id,
            role=ProjectRole.ADMIN,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )

    assets = {name: RawMaterialAsset(name=name, created_at=now, updated_at=now) for name in ["СНХЗ", "БСЗ", "ВОС"]}
    for asset in assets.values():
        session.add(asset)
    await session.flush()

    materials = {name: RawMaterial(name=name, created_at=now, updated_at=now) for name in ["Аммиак", "Метанол"]}
    for material in materials.values():
        session.add(material)
    await session.flush()

    arm_by_key: dict[tuple[str, str], AssetRawMaterial] = {}
    for asset_name, material_name in [
        ("СНХЗ", "Аммиак"),
        ("СНХЗ", "Метанол"),
        ("БСЗ", "Аммиак"),
        ("ВОС", "Метанол"),
    ]:
        arm = AssetRawMaterial(
            asset_id=assets[asset_name].id,
            raw_material_id=materials[material_name].id,
            created_at=now,
            updated_at=now,
        )
        session.add(arm)
        arm_by_key[(asset_name, material_name)] = arm
    await session.flush()

    for month in [date(2026, 8, 1), date(2026, 9, 1)]:
        session.add(MovementPeriod(report_month=month))

    for basis in ["FOB", "FCA"]:
        session.add(DeliveryBasis(name=basis, created_at=now, updated_at=now))
    await session.flush()

    for (_, _), arm in arm_by_key.items():
        session.add(
            RawMaterialPrice(
                asset_raw_material_id=arm.id,
                delivery_basis_id=None,
                fact_2025=1.0,
                business_plan_quantity_tons=100.0,
                business_plan_usd_per_ton=200.0,
                business_plan_rub_per_ton=20000.0,
                delivery_cost_rub=500.0,
                storage_standard_tons=50.0,
                created_at=now,
                updated_at=now,
            )
        )

    filled_arm = arm_by_key[("СНХЗ", "Аммиак")]
    session.add(
        MaterialMovement(
            asset_raw_material_id=filled_arm.id,
            report_month=date(2026, 8, 1),
            demand_plan_tons=100.0,
            consumption_fact_tons=80.0,
            delivery_plan_tons=90.0,
            delivery_fact_tons=70.0,
            balance_start_tons=10.0,
            balance_on_site_tons=5.0,
            balance_in_transit_tons=2.0,
            balance_end_tons=1.0,
            deviation_reason="Недопоставка",
            created_at=now,
            updated_at=now - timedelta(hours=1),
        )
    )
    for (_, _), arm in arm_by_key.items():
        if arm.id == filled_arm.id:
            continue
        session.add(
            MaterialMovement(
                asset_raw_material_id=arm.id,
                report_month=date(2026, 8, 1),
                created_at=now,
                updated_at=now,
            )
        )
    for (_, _), arm in arm_by_key.items():
        session.add(
            MaterialMovement(
                asset_raw_material_id=arm.id,
                report_month=date(2026, 9, 1),
                created_at=now,
                updated_at=now,
            )
        )
    await _seed_renovation(session, now=now, admin=admin)
    await _seed_kppk(session, now=now, admin=admin)
    await session.commit()


async def _seed_renovation(session: AsyncSession, *, now: datetime, admin: User) -> None:
    """Renovation seed: project, assets, reference values, admin role, working plan."""
    from decimal import Decimal

    from src.auth.models import Project
    from src.renovation.constants import R_ASSETS, RENOVATION_PROJECT_KEY, RENOVATION_PROJECT_NAME
    from src.renovation.models import RAsset, RProjectRole, RUserRole
    from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
    from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id

    project = Project(key=RENOVATION_PROJECT_KEY, name=RENOVATION_PROJECT_NAME)
    session.add(project)
    await session.flush()
    session.add(
        RUserRole(
            user_id=admin.id,
            project_id=project.id,
            role=RProjectRole.ADMIN,
            is_active=True,
            created_at=now,
            updated_at=now,
            updater_user_id=None,
        )
    )

    assets = {
        code: RAsset(code=code, repair_code=repair_code, name=name, created_at=now)
        for code, repair_code, name in R_ASSETS
    }
    for asset in assets.values():
        session.add(asset)
    await session.flush()

    for catalog, label in REFERENCE_OPTIONS:
        value = RenovationReferenceValue(
            id=reference_option_id(catalog, label),
            catalog=catalog,
            label=label,
        )
        session.add(value)
    await session.flush()

    working = RenovationPlan(
        project_id=project.id,
        state=RPlanState.WORKING,
        baseline_generation=1,
        created_at=now,
        created_by="seed",
        updated_at=now,
        updater_user_id=None,
    )
    session.add(working)
    await session.flush()

    # Positions are 1-based everywhere in production: the Excel seed reads the
    # «№ п/п» column and add_row starts at 1. Keep the fixture on the same
    # base — the UI shows the stored position verbatim («№» column).
    for position, (code, defect, d1, d2) in enumerate(
        [
            ("bsk", "1382467", Decimal(10), Decimal(20)),
            ("vos", "2247000", Decimal(5), Decimal(7)),
        ],
        start=1,
    ):
        # col_003 is a plain typed value now (the generator is off): the fixture
        # stores the codes a real plan carries, like the Excel seed does.
        repair_code = next(rc for asset_code, rc, _ in R_ASSETS if asset_code == code).upper()
        session.add(
            RenovationPlanRow(
                plan_id=working.id,
                asset_id=assets[code].id,
                position=position,
                baseline_generation=1,
                revision=0,
                created_at=now,
                updated_at=now,
                updater_user_id=None,
                col_003=f"2027_{repair_code}_1.1.1_{defect}",
                col_011="Шкаф ПЛК" if code == "bsk" else "Насос НК",
                col_016="Ремонтный бюджет",
                col_017=reference_option_id("work_category", "Остановочный капитальный ремонт"),
                col_018=reference_option_id("work_type", "ТР"),
                col_019=reference_option_id("cost_allocation", "OPEX"),
                col_020=reference_option_id("execution_method", "Собственные силы"),
                col_023=defect,
                col_024=date(2027, 1, 1),
                col_025=date(2027, 1, 31),
                col_028=d1,
                col_029=d2,
                col_189="1.1.1.",
            )
        )
    # The production seed materializes the calculated fields after importing the
    # plan; mirror that so the fixture rows carry the same derived values.
    from src.renovation.service import recompute_plan_rows

    await session.flush()
    await recompute_plan_rows(session, plan_id=working.id)


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """DB session with a clean seed per test (isolation from commits inside tests)."""
    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        config.attributes["connection"] = conn.sync_connection
        await conn.run_sync(lambda _: command.upgrade(config, "head"))
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await _seed_session(session)
        try:
            yield session
        finally:
            await session.rollback()
            # Every test recreates the schema, so a cached row set from the
            # previous test would describe tables that no longer exist. The
            # caches key on their own data token, which makes them correct in
            # production; here the whole schema changes underneath them.
            from src.common.read_cache import clear_read_cache

            clear_read_cache()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """FastAPI test HTTP client with the test DB injected."""
    from src.database import get_session
    from src.main import app

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
            from sqlmodel import col, select

            from src.collab.models import OutboxEvent
            from src.collab.store import get_collab_store

            events = (await db_session.exec(select(OutboxEvent).order_by(col(OutboxEvent.id)))).all()
            for event in events:
                await get_collab_store().publish(event.room, event.payload)
                await db_session.delete(event)
            await db_session.commit()
        except BaseException:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def store(isolated_store, db_session):
    """Inspect durable history while using the real configured lease transport."""
    from src.collab.persistence import JournalStore

    return JournalStore(db_session)


@pytest.fixture(autouse=True)
async def isolated_store():
    """Reset only an explicitly opted-in disposable Valkey DB, never the app DB."""
    from redis.asyncio import Redis

    from src.collab.store import ValkeyStore, get_collab_store
    from src.collab.ws import get_room_manager

    get_collab_store.cache_clear()
    get_room_manager.cache_clear()
    if COLLAB_BACKEND == "valkey":
        url = os.environ["VALKEY_URL"]
        if urlparse(url).path != "/15" or os.environ.get("TEST_VALKEY_RESET") != "1":
            pytest.fail("Real-store suite requires disposable TEST_VALKEY_URL ending in /15 and TEST_VALKEY_RESET=1")
    instance = get_collab_store()
    cleanup = Redis.from_url(os.environ["VALKEY_URL"]) if isinstance(instance, ValkeyStore) else None
    if cleanup is not None:
        await cleanup.flushdb()
    try:
        yield instance
    finally:
        if cleanup is not None:
            await cleanup.flushdb()
            await cleanup.aclose()
        await instance.close()
        get_collab_store.cache_clear()
        get_room_manager.cache_clear()


async def _seed_kppk(session: AsyncSession, *, now: datetime, admin: User) -> None:
    """КППК seed: project, admin role, ДЗО and a few products/links for the matrix."""
    from src.auth.models import Project, ProjectRole, UserRole
    from src.kppk.constants import KPPK_PROJECT_KEY, KPPK_PROJECT_NAME
    from src.kppk.models import KppkOrg, KppkOrgProduct, KppkProduct, KppkRaw

    project = Project(key=KPPK_PROJECT_KEY, name=KPPK_PROJECT_NAME, created_at=now)
    session.add(project)
    await session.flush()
    session.add(
        UserRole(
            user_id=admin.id,
            project_id=project.id,
            role=ProjectRole.ADMIN,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    # Source numbers stand for raw.kppk_d_org.id; reads sort by name, so
    # «Березниковский…» (1) leads «СНХЗ» (2).
    orgs = {
        name: KppkOrg(code=code, name=name, num=num, created_at=now, updated_at=now, updater_user_id=None)
        for code, name, num in [
            ("БСЗ", "АО «Березниковский содовый завод»", 1),
            ("СНХЗ", "АО «СНХЗ»", 2),
        ]
    }
    for org in orgs.values():
        session.add(org)
    await session.flush()
    # Source numbers mirror the dictionary order; «Сода ... А» stands for a
    # product the matrix brought in without a number (NULL sorts last).
    products = {
        name: KppkProduct(
            name=name,
            product_num=num,
            created_at=now,
            updated_at=now,
            updater_user_id=None,
        )
        for name, num in [
            ("Сода кальцинированная марки А", None),
            ("Сода кальцинированная марки Б", 2),
            ("Сульфат натрия", 1),
        ]
    }
    for product in products.values():
        session.add(product)
    await session.flush()
    org_products = [
        KppkOrgProduct(
            org_id=orgs["АО «СНХЗ»"].id,
            product_id=products["Сульфат натрия"].id,
            created_at=now,
            updated_at=now,
            updater_user_id=None,
        ),
        KppkOrgProduct(
            org_id=orgs["АО «Березниковский содовый завод»"].id,
            product_id=products["Сода кальцинированная марки Б"].id,
            created_at=now,
            updated_at=now,
            updater_user_id=None,
        ),
    ]
    for link in org_products:
        session.add(link)
    session.add(KppkRaw(name="Резина", raw_num=2, created_at=now, updated_at=now, updater_user_id=None))
    session.add(KppkRaw(name="Каучук", raw_num=1, created_at=now, updated_at=now, updater_user_id=None))
    # «Реализация» rows for the June period of «СНХЗ» (source org id 2). The
    # products are seeded in a deliberately WRONG insertion order: the read must
    # sort them by the «Продуктовые позиции» number, not by the copy's text.

    from src.kppk.models import KppkRealization, KppkRealizationPlan

    for product, region in [
        ("Сода кальцинированная марки Б", "RU"),
        ("Сульфат натрия", "RU"),
        ("Сода кальцинированная марки Б", "KZ"),
        # A name the dictionary does not hold: it sorts last.
        ("Неизвестный продукт", "RU"),
    ]:
        session.add(
            KppkRealizationPlan(
                org_id=orgs["АО «СНХЗ»"].id,
                period_date=date(2026, 6, 1),
                rep_date=date(2026, 6, 30),
                product=product,
                region=region,
                month_bp_plan=Decimal("10.000"),
            )
        )
        session.add(
            KppkRealization(
                org_id=orgs["АО «СНХЗ»"].id,
                period_date=date(2026, 6, 1),
                rep_date=date(2026, 6, 30),
                product=product,
                region=region,
                fact=Decimal("1.000"),
            )
        )
    await session.flush()


async def raw_materials_role(db_session: AsyncSession):
    """Return the admin's raw_materials UserRole (the admin now also holds a kppk link)."""
    from sqlmodel import col, select

    from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
    from src.auth.models import Project, UserRole

    return (
        await db_session.exec(
            select(UserRole)
            .join(Project, col(Project.id) == col(UserRole.project_id))
            .where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY)
        )
    ).one()


@pytest.fixture
def presence_expiry():
    """Read expiration without changing store behavior."""
    from redis.asyncio import Redis

    from src.collab.store import MemoryStore, room_presence_key

    async def read(instance, room: str, user_id: str) -> float | None:
        from src.collab.store import get_collab_store

        instance = get_collab_store()
        if isinstance(instance, MemoryStore):
            return await instance.presence_expires_at(room, user_id)
        async with Redis.from_url(os.environ["VALKEY_URL"]) as connection:
            raw = await connection.hget(room_presence_key(room), user_id)
            return json.loads(raw)["expiresAt"] if raw else None

    return read


def signed_identity_headers(
    *,
    email: str | None = None,
    name: str | None = None,
    preferred_username: str | None = None,
    subject: str | None = None,
    issuer: str | None = None,
    given_name: str | None = None,
    family_name: str | None = None,
    email_verified: str | None = None,
    timestamp: int | str | None = None,
) -> dict[str, str]:
    """Build signed identity headers the way the frontend does."""
    from hashlib import sha256
    from hmac import new
    from time import time
    from urllib.parse import quote

    values = {
        "X-ReportCollector-User-Email": email,
        "X-ReportCollector-User-Name": name,
        "X-ReportCollector-User-Sub": subject,
        "X-ReportCollector-User-Issuer": issuer,
        "X-ReportCollector-Preferred-Username": preferred_username,
        "X-ReportCollector-Given-Name": given_name,
        "X-ReportCollector-Family-Name": family_name,
        "X-ReportCollector-Email-Verified": email_verified,
    }
    headers: dict[str, str] = {
        header: quote(value) if value is not None else "" for header, value in values.items() if value is not None
    }
    stamp = str(int(time()) if timestamp is None else timestamp)
    payload = "\n".join([stamp, *(values[header] or "" for header in values)])
    signature = new(os.environ["AUTH_SECRET"].encode(), payload.encode(), sha256).hexdigest()
    headers["X-ReportCollector-Identity-Timestamp"] = stamp
    headers["X-ReportCollector-Identity-Signature"] = signature
    return headers
