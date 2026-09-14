"""Authorization, archive immutability and real transaction failure regressions."""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from conftest import signed_identity_headers
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole, User, UserRole
from src.collab.events import publish_cell_changed
from src.collab.models import CellState, EditStack, OutboxEvent
from src.collab.persistence import JournalStore
from src.raw_materials.models import MaterialMovement, RawMaterialAsset, RawMaterialUserAsset
from src.raw_materials.schemas import CellUpdate
from src.raw_materials.service import update_movement_cells
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserRole
from src.renovation.plan import RenovationPlanRow
from src.renovation.schemas import CellUpdate as RCellUpdate
from src.renovation.service import update_plan_cells

HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
MONTH = date(2026, 8, 1)


async def admin_and_rows(session):
    user = (await session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    rows = (await session.exec(select(MaterialMovement).where(MaterialMovement.report_month == MONTH))).all()
    assets = list((await session.exec(select(RawMaterialAsset))).all())
    return user, rows, assets


async def patch(client, row_id, value=17):
    return await client.patch(
        "/api/raw-materials/movement",
        headers=HEADERS,
        params={"session_id": "guarantee"},
        json={
            "reportMonth": str(MONTH),
            "updates": [{"rowId": str(row_id), "field": "balanceOnSiteTons", "value": value}],
        },
    )


async def test_worker_without_assets_reads_nothing_and_cannot_patch(client, db_session):
    _, rows, _ = await admin_and_rows(db_session)
    row_id = rows[0].id
    role = (await db_session.exec(select(UserRole))).one()
    role.role = ProjectRole.ASSET_WORKER
    await db_session.commit()
    for path in ["movement?report_month=2026-08-01", "prices"]:
        response = await client.get(f"/api/raw-materials/{path}", headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == []
    assert (await patch(client, row_id)).status_code in {403, 404, 422}


async def test_renovation_worker_without_assets_cannot_read_even_unassigned_drafts(client, db_session):
    row = (await db_session.exec(select(RenovationPlanRow))).first()
    row.asset_id = None
    link = (await db_session.exec(select(RUserRole))).one()
    link.role = RProjectRole.ASSET_WORKER
    link.block = RBlock.TECH
    await db_session.commit()
    for path in ["/api/renovation/plan", "/api/renovation/plan/rows"]:
        response = await client.get(path, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["rows"] == []


@pytest.mark.parametrize("role", [RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER])
async def test_central_roles_see_all_assets_without_assignments(client, db_session, role):
    link = (await db_session.exec(select(RUserRole))).one()
    link.role = role
    link.block = RBlock.TECH if role == RProjectRole.BLOCK_OWNER else None
    await db_session.commit()
    response = await client.get("/api/renovation/plan/rows", headers=HEADERS)
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 2


async def test_snapshot_and_export_are_asset_scoped(client, db_session):
    response = await client.post("/api/raw-materials/movement/fix", headers=HEADERS, json={"reportMonth": str(MONTH)})
    assert response.status_code == 200
    snapshot_id = response.json()["id"]
    role = (await db_session.exec(select(UserRole))).one()
    asset = (await db_session.exec(select(RawMaterialAsset).where(RawMaterialAsset.name == "БСЗ"))).one()
    role.role = ProjectRole.ASSET_WORKER
    db_session.add(RawMaterialUserAsset(user_id=role.user_id, asset_id=asset.id))
    await db_session.commit()
    response = await client.get(f"/api/raw-materials/movement/snapshot/{snapshot_id}", headers=HEADERS)
    assert response.status_code == 200
    assert {row["assetName"] for row in response.json()["rows"]} == {"БСЗ"}
    from src.raw_materials.service import read_snapshot_export_rows

    _, rows = await read_snapshot_export_rows(db_session, UUID(snapshot_id), [asset])
    assert {row.assetName for row in rows} == {"БСЗ"}


async def test_closed_month_rejects_direct_patch(client, db_session):
    _, rows, _ = await admin_and_rows(db_session)
    row_id = rows[0].id
    assert (
        await client.post("/api/raw-materials/movement/fix", headers=HEADERS, json={"reportMonth": str(MONTH)})
    ).status_code == 200
    assert (await patch(client, row_id)).status_code == 422


async def test_other_session_lease_blocks_patch(client, db_session, isolated_store):
    user, rows, _ = await admin_and_rows(db_session)
    row_id = rows[0].id
    from src.collab.store import lease_key

    await isolated_store.acquire_lease(
        lease_key(f"rm_material_movements/{row_id}/balanceOnSiteTons", project="raw_materials"),
        {"userId": str(user.id), "sessionId": "another-tab"},
    )
    assert (await patch(client, row_id)).status_code == 422


async def test_rollback_removes_edit_history_revisions_and_event(db_session, test_engine):
    user, rows, assets = await admin_and_rows(db_session)
    row_id, old_value, user_id = rows[0].id, rows[0].balance_on_site_tons, user.id
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = await session.get(User, user_id)
        assets = list((await session.exec(select(RawMaterialAsset))).all())
        applied = await update_movement_cells(
            session,
            report_month=MONTH,
            user=user,
            assets=assets,
            updates=[CellUpdate(rowId=row_id, field="balanceOnSiteTons", value=77)],
            session_id="rollback",
        )
        await publish_cell_changed(
            JournalStore(session),
            project_key="raw_materials",
            section="materialMovement",
            report_month=str(MONTH),
            user_id=user_id,
            user_name="Admin",
            user_email="",
            cells=applied,
        )

        def fail_commit(_session) -> None:
            message = "injected commit failure"
            raise RuntimeError(message)

        event.listen(session.sync_session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            await session.commit()
        await session.rollback()
    async with factory() as session:
        restored = await session.get(MaterialMovement, row_id)
        assert restored is not None
        assert restored.balance_on_site_tons == old_value
        for model in [EditStack, CellState, OutboxEvent]:
            assert list((await session.exec(select(model))).all()) == []


async def test_outbox_retries_without_losing_committed_data(db_session, test_engine, monkeypatch, isolated_store):
    from src.collab import outbox

    user, rows, assets = await admin_and_rows(db_session)
    applied = await update_movement_cells(
        db_session,
        report_month=MONTH,
        user=user,
        assets=assets,
        updates=[CellUpdate(rowId=rows[0].id, field="balanceOnSiteTons", value=44)],
        session_id="outbox",
    )
    row_id = rows[0].id
    await publish_cell_changed(
        JournalStore(db_session),
        project_key="raw_materials",
        section="materialMovement",
        report_month=str(MONTH),
        user_id=user.id,
        user_name="Admin",
        user_email="",
        cells=applied,
    )
    await db_session.commit()
    monkeypatch.setattr(
        outbox, "async_session_factory", async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    )
    sender = AsyncMock(side_effect=RedisConnectionError("injected outage"))
    monkeypatch.setattr(isolated_store, "publish", sender)
    with pytest.raises(RedisConnectionError):
        await outbox.dispatch_once()
    assert (await db_session.get(MaterialMovement, row_id)).balance_on_site_tons == 44
    assert len((await db_session.exec(select(OutboxEvent))).all()) == 1
    await db_session.rollback()
    sender.side_effect = None
    await outbox.dispatch_once()
    assert list((await db_session.exec(select(OutboxEvent))).all()) == []
    assert sender.await_count == 2


async def test_undo_cannot_mutate_archive(client, db_session):
    row = (await db_session.exec(select(RenovationPlanRow))).first()
    row_id = row.id
    response = await client.patch(
        "/api/renovation/plan",
        headers=HEADERS,
        params={"session_id": "archive"},
        json={"updates": [{"rowId": str(row_id), "field": "col_011", "value": "Saved"}]},
    )
    assert response.status_code == 200
    stack = (await db_session.exec(select(EditStack))).one()
    actions, user_id = stack.actions, stack.user_id
    assert (await client.post("/api/renovation/plan/submit", headers=HEADERS)).status_code == 200
    assert (await client.post("/api/renovation/plan/approve", headers=HEADERS)).status_code == 200
    # Even a retained stale action must be rejected independently of history clearing.
    db_session.add(
        EditStack(
            user_id=user_id,
            session_id="archive",
            scope="renovationPlan/",
            actions=actions,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()
    response = await client.post(
        "/api/renovation/undo", headers=HEADERS, params={"session_id": "archive"}, json={"section": "renovationPlan"}
    )
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert (await db_session.get(RenovationPlanRow, row_id, populate_existing=True)).col_011 == "Saved"


async def test_concurrent_renovation_edits_preserve_both_values_and_formula(db_session, test_engine):
    user = (await db_session.exec(select(User))).one()
    row = (await db_session.exec(select(RenovationPlanRow))).first()
    user_id, row_id = user.id, row.id
    await db_session.commit()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def edit(field, value) -> None:
        async with factory() as session:
            user = await session.get(User, user_id)
            assets = list((await session.exec(select(RAsset))).all())
            await update_plan_cells(
                session,
                user=user,
                roles=[RProjectRole.ADMIN],
                assets=assets,
                block=None,
                updates=[RCellUpdate(rowId=row_id, field=field, value=value)],
                session_id=field,
            )
            await session.commit()

    await asyncio.gather(edit("col_028", 31), edit("col_029", 41))
    actual = await db_session.get(RenovationPlanRow, row_id, populate_existing=True)
    assert actual.col_028 == Decimal(31)
    assert actual.col_029 == Decimal(41)
    assert actual.col_031 == Decimal(72) + (actual.col_030 or Decimal(0))
    assert actual.revision == 2


async def test_undo_rechecks_revoked_asset_access(client, db_session):
    _, rows, _ = await admin_and_rows(db_session)
    row_id = rows[0].id
    assert (await patch(client, row_id, 81)).status_code == 200
    role = (await db_session.exec(select(UserRole))).one()
    role.role = ProjectRole.ASSET_WORKER
    await db_session.commit()
    response = await client.post(
        "/api/raw-materials/undo",
        headers=HEADERS,
        params={"session_id": "guarantee"},
        json={"section": "materialMovement", "reportMonth": str(MONTH)},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert (await db_session.get(MaterialMovement, row_id, populate_existing=True)).balance_on_site_tons == 81


async def test_duplicate_cells_in_batch_undo_to_original_value(client, db_session):
    _, rows, _ = await admin_and_rows(db_session)
    row_id, original = rows[0].id, rows[0].balance_on_site_tons
    response = await client.patch(
        "/api/raw-materials/movement",
        headers=HEADERS,
        params={"session_id": "duplicate"},
        json={
            "reportMonth": str(MONTH),
            "updates": [{"rowId": str(row_id), "field": "balanceOnSiteTons", "value": value} for value in [13, 27]],
        },
    )
    assert response.status_code == 200
    response = await client.post(
        "/api/raw-materials/undo",
        headers=HEADERS,
        params={"session_id": "duplicate"},
        json={"section": "materialMovement", "reportMonth": str(MONTH)},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert (await db_session.get(MaterialMovement, row_id, populate_existing=True)).balance_on_site_tons == original


async def test_expired_history_is_unavailable_and_pruned(db_session):
    user, _, _ = await admin_and_rows(db_session)
    repository = JournalStore(db_session)
    await repository.push_actions(str(user.id), "expired", "materialMovement/", [{"id": 1, "undone": False}])
    stack = (await db_session.exec(select(EditStack))).one()
    stack.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    assert await repository.list_actions(str(user.id), "expired", "materialMovement/") == []
    await repository.prune_expired()
    assert list((await db_session.exec(select(EditStack))).all()) == []


async def test_readiness_checks_database_and_valkey(client, test_engine, monkeypatch, isolated_store):
    from src import main

    monkeypatch.setattr(
        main, "async_session_factory", async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    )
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.headers["x-request-id"]
    monkeypatch.setattr(isolated_store, "room_members", AsyncMock(side_effect=RedisConnectionError("secret address")))
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize("logger_name", ["uvicorn.access", "uvicorn.error"])
def test_transport_logs_redact_ticket_without_destroying_format_arguments(logger_name):
    from src.app_logging import CredentialQueryFilter

    record = logging.LogRecord(
        logger_name,
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" %d',
        ("127.0.0.1", "/api/ws?section=assets&ticket=secret-ticket&session_id=page", 101),
        None,
    )
    assert CredentialQueryFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-ticket" not in rendered
    assert "ticket=[REDACTED]&session_id=page" in rendered
    assert rendered.endswith("101")


async def test_outbox_worker_retries_and_prunes_expired_history(db_session, test_engine, monkeypatch):
    from src.collab import outbox

    user, _, _ = await admin_and_rows(db_session)
    db_session.add(
        EditStack(
            user_id=str(user.id),
            session_id="stale",
            scope="scope",
            actions=[],
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        outbox, "async_session_factory", async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    )
    dispatch = AsyncMock(side_effect=[RedisConnectionError("outage"), None])
    monkeypatch.setattr(outbox, "dispatch_once", dispatch)
    delays = []

    async def sleep(delay) -> None:
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(outbox.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await outbox.run_outbox()
    assert dispatch.await_count == 2
    assert delays == [1, 0.05, 0.05]
    assert list((await db_session.exec(select(EditStack))).all()) == []


async def test_session_dependency_commits_and_rolls_back(test_engine, monkeypatch):
    from src import database

    monkeypatch.setattr(
        database, "async_session_factory", async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    )
    dependency = database.get_session()
    session = await anext(dependency)
    session.add(OutboxEvent(room="committed", payload={}))
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)
    dependency = database.get_session()
    session = await anext(dependency)
    session.add(OutboxEvent(room="rolled-back", payload={}))
    await session.flush()
    with pytest.raises(RuntimeError, match="request failed"):
        await dependency.athrow(RuntimeError("request failed"))
    async with database.async_session_factory() as check:
        assert [item.room for item in (await check.exec(select(OutboxEvent))).all()] == ["committed"]
