"""Shared fixtures for ReportCollector tests."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.collab.store import MemoryStore

# Env vars must be set before the first src.* import, because src.database
# caches settings at module level.
os.environ.setdefault("AUTH_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("AUTH_SEED_ADMIN_EMAIL", "admin@example.ru")
os.environ.setdefault("AUTH_SEED_ADMIN_PASSWORD", "AdminPassword123")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "strong_password")
os.environ.setdefault("POSTGRES_DB", "dwh")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_SCHEMA", "rc")
os.environ.setdefault("BACKEND_CORS_ORIGINS", '["http://localhost:5000","http://127.0.0.1:5000"]')
os.environ.setdefault("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:strong_password@localhost:5432/dwh")
os.environ.setdefault("TEST_SCHEMA", "rc_test")
os.environ.setdefault("VALKEY_URL", "")

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]
TEST_SCHEMA = os.environ["TEST_SCHEMA"]
BACKEND_DIR = Path(__file__).resolve().parents[1]


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
        key="admin@example.ru",
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
            storage_standard_tons=50.0,
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
    await session.commit()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """DB session with a clean seed per test (isolation from commits inside tests)."""
    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        await conn.run_sync(SQLModel.metadata.create_all)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await _seed_session(session)
        yield session
        await session.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """FastAPI test HTTP client with the test DB injected."""
    from src.database import get_session
    from src.main import app

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def store() -> MemoryStore:
    """Collaboration store (in-memory in tests)."""
    from src.collab.store import MemoryStore, get_collab_store

    instance = get_collab_store()
    assert isinstance(instance, MemoryStore)
    return instance


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
) -> dict[str, str]:
    """Build signed identity headers the way the frontend does."""
    from hashlib import sha256
    from hmac import new
    from time import time
    from urllib.parse import quote

    values = {
        "X-ReportCollector-User-Key": preferred_username or email or subject,
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
    timestamp = str(int(time()))
    payload = "\n".join([timestamp, *(values[header] or "" for header in values)])
    signature = new(os.environ["AUTH_SECRET"].encode(), payload.encode(), sha256).hexdigest()
    headers["X-ReportCollector-Identity-Timestamp"] = timestamp
    headers["X-ReportCollector-Identity-Signature"] = signature
    return headers
