"""Тесты фиксации формы «Движение»."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from conftest import signed_identity_headers
from httpx import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.raw_materials.models import (
    AssetRawMaterial,
    MaterialMovement,
    MovementPeriod,
    ReportSnapshot,
    SnapshotMaterialMovement,
)

VIEWER_HEADERS = signed_identity_headers(email="viewer@example.local", name="Viewer User")
ADMIN_HEADERS = signed_identity_headers(email="admin@example.local", name="Admin User")


def _now() -> datetime:
    return datetime.now(UTC)


async def _fill_month(session: AsyncSession, report_month: date) -> None:
    """Заполняет все строки «Движения» месяца полностью (без businessPlanTons из цен)."""
    rows = (
        await session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == report_month))
    ).all()
    now = _now()
    for row in rows:
        row.demand_plan_tons = 100.0
        row.consumption_fact_tons = 80.0
        row.delivery_plan_tons = 90.0
        row.delivery_fact_tons = 70.0
        row.balance_start_tons = 10.0
        row.balance_on_site_tons = 5.0
        row.balance_in_transit_tons = 2.0
        row.balance_end_tons = 1.0
        row.storage_standard_tons = 50.0
        row.deviation_reason = "Недопоставка"
        row.updated_at = now
    await session.flush()


async def _ensure_rows(session: AsyncSession, report_month: date) -> None:
    """Создаёт недостающие строки «Движения» месяца для всех arm (без заполнения)."""
    arms = (await session.exec(select(AssetRawMaterial))).all()
    now = _now()
    for arm in arms:
        exists = (
            await session.exec(
                select(MaterialMovement).where(
                    col(MaterialMovement.asset_raw_material_id) == arm.id,
                    col(MaterialMovement.report_month) == report_month,
                )
            )
        ).first()
        if exists is None:
            session.add(
                MaterialMovement(
                    asset_raw_material_id=arm.id,
                    report_month=report_month,
                    created_at=now,
                    updated_at=now,
                )
            )
    await session.flush()


async def _fix(client: AsyncClient, report_month: date) -> tuple[int, dict]:
    response = await client.post(
        "/api/raw-materials/movement/fix",
        headers=ADMIN_HEADERS,
        json={"reportMonth": report_month.isoformat()},
    )
    return response.status_code, response.json()


async def test_fix_requires_admin(client: AsyncClient) -> None:
    response = await client.post(
        "/api/raw-materials/movement/fix",
        headers=VIEWER_HEADERS,
        json={"reportMonth": "2026-08-01"},
    )
    assert response.status_code == 403


async def test_fix_rejects_non_first_month(client: AsyncClient) -> None:
    status, body = await _fix(client, date(2026, 9, 1))
    assert status == 409
    assert "первый" in body["detail"]


async def test_fix_rejects_incomplete_month(client: AsyncClient, db_session: AsyncSession) -> None:
    status, body = await _fix(client, date(2026, 8, 1))
    assert status == 409
    assert "незаполненных строк" in body["detail"]
    assert "3 из 4" in body["detail"]


async def test_fix_creates_snapshot(
    client: AsyncClient,
    db_session: AsyncSession,
    store,
) -> None:
    await _ensure_rows(db_session, date(2026, 8, 1))
    await _fill_month(db_session, date(2026, 8, 1))
    status, body = await _fix(client, date(2026, 8, 1))
    assert status == 200
    assert body["reportMonth"] == "2026-08-01"
    assert body["isFinal"] is False
    snapshot_id = UUID(body["id"])

    snapshots = (await db_session.exec(select(ReportSnapshot))).all()
    assert len(snapshots) == 1
    assert snapshots[0].fixed_by_email == "admin@example.local"

    rows = (
        await db_session.exec(
            select(SnapshotMaterialMovement).where(col(SnapshotMaterialMovement.snapshot_id) == snapshot_id)
        )
    ).all()
    assert len(rows) == 4
    filled = next(row for row in rows if row.asset_name == "СНХЗ" and row.raw_material_name == "Аммиак")
    assert filled.business_plan_tons == 100.0
    assert filled.demand_deviation_tons == 20.0
    assert filled.delivery_deviation_tons == 20.0
    assert filled.deviation_reason == "Недопоставка"

    assert await store.list_actions("any-user") == []

    meta = await client.get(
        "/api/raw-materials/movement/meta",
        headers=ADMIN_HEADERS,
        params={"report_month": "2026-08-01"},
    )
    meta_body = meta.json()
    assert meta_body["isComplete"] is True
    assert meta_body["isFinal"] is False
    assert len(meta_body["snapshots"]) == 1
    assert meta_body["snapshots"][0]["id"] == body["id"]

    # Архивная версия сохраняет дефолтный порядок: сырьё, затем актив.
    view = await client.get(
        f"/api/raw-materials/movement/snapshot/{snapshot_id}",
        headers=ADMIN_HEADERS,
    )
    assert view.status_code == 200
    view_rows = view.json()["rows"]
    pairs = [(row["rawMaterialName"], row["assetName"]) for row in view_rows]
    assert pairs == sorted(pairs)


async def test_fix_preview_overwrites_and_keeps_history_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _ensure_rows(db_session, date(2026, 8, 1))
    await _fill_month(db_session, date(2026, 8, 1))
    _, first = await _fix(client, date(2026, 8, 1))
    rows = (
        await db_session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == date(2026, 8, 1)))
    ).all()
    rows[0].delivery_fact_tons = 75.0
    await db_session.flush()
    status, second = await _fix(client, date(2026, 8, 1))
    assert status == 200
    assert second["id"] != first["id"]

    snapshots = (await db_session.exec(select(ReportSnapshot))).all()
    assert len(snapshots) == 1
    assert snapshots[0].previous_snapshot_id == UUID(first["id"])

    view = await client.get(f"/api/raw-materials/movement/snapshot/{second['id']}", headers=ADMIN_HEADERS)
    assert view.status_code == 200
    assert view.json()["comparisonSnapshotId"] == first["id"]


async def test_fix_final_closes_period_and_opens_next(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    past_month = date(2026, 7, 1)
    db_session.add(MovementPeriod(report_month=past_month))
    await db_session.flush()
    arms = (await db_session.exec(select(AssetRawMaterial))).all()
    now = _now()
    for arm in arms:
        db_session.add(
            MaterialMovement(
                asset_raw_material_id=arm.id,
                report_month=past_month,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()
    await _fill_month(db_session, past_month)

    status, body = await _fix(client, past_month)
    assert status == 200
    assert body["isFinal"] is True

    assert (await db_session.get(MovementPeriod, past_month)) is None
    next_month = date(2026, 10, 1)
    assert await db_session.get(MovementPeriod, next_month) is not None
    next_rows = (
        await db_session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == next_month))
    ).all()
    assert len(next_rows) == len(arms)


async def test_fix_final_cannot_be_overwritten(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    past_month = date(2026, 7, 1)
    db_session.add(MovementPeriod(report_month=past_month))
    arms = (await db_session.exec(select(AssetRawMaterial))).all()
    now = _now()
    for arm in arms:
        db_session.add(
            MaterialMovement(
                asset_raw_material_id=arm.id,
                report_month=past_month,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()
    await _fill_month(db_session, past_month)
    status, _ = await _fix(client, past_month)
    assert status == 200

    status, body = await _fix(client, past_month)
    assert status == 409
    assert "Финальную" in body["detail"]


async def test_fix_blocked_by_lease(client: AsyncClient, db_session: AsyncSession, store) -> None:
    await _ensure_rows(db_session, date(2026, 8, 1))
    await _fill_month(db_session, date(2026, 8, 1))
    row = (
        await db_session.exec(select(MaterialMovement).where(col(MaterialMovement.report_month) == date(2026, 8, 1)))
    ).first()
    assert row is not None
    from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
    from src.collab.store import lease_key

    await store.acquire_lease(
        lease_key(f"rm_material_movement/{row.id}/balance_on_site_tons", project=RAW_MATERIALS_PROJECT_KEY),
        {"userId": "someone", "name": "Иванов"},
    )
    status, body = await _fix(client, date(2026, 8, 1))
    assert status == 409
    assert "Фиксация временно недоступна" in body["detail"]


async def test_snapshot_view_not_found(client: AsyncClient) -> None:
    response = await client.get(
        "/api/raw-materials/movement/snapshot/00000000-0000-0000-0000-000000000000",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404


async def test_movement_meta_reports_incomplete_before_fix(client: AsyncClient) -> None:
    meta = await client.get(
        "/api/raw-materials/movement/meta",
        headers=ADMIN_HEADERS,
        params={"report_month": "2026-08-01"},
    )
    body = meta.json()
    assert body["isComplete"] is False
    assert body["isFinal"] is False
    assert body["snapshots"] == []
