"""WebSocket authorization regression tests against the actual SQL schema."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import WebSocket
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_collab import FakeWebSocket, _wait_until

from src.auth.models import ProjectRole, UserRole
from src.auth.service import verify_signed_identity
from src.collab.access import RoomAccess, RoomAccessDeniedError

if TYPE_CHECKING:
    from src.collab.store import CollabStore
from src.collab.ws import RoomManager, _handle_message, _resolve_raw_materials_access, raw_materials_ws
from src.raw_materials.models import AssetRawMaterial, MaterialMovement, RawMaterialUserAsset


def access_for(db_session, *, section="materialMovement", month="2026-08-01"):
    fake = FakeWebSocket(project="raw_materials")
    return RoomAccess(
        lambda: AsyncSession(db_session.bind, expire_on_commit=False),
        verify_signed_identity(fake.headers),
        _resolve_raw_materials_access,
        "raw_materials",
        section,
        month,
    )


async def restrict_admin_to_asset(db_session, asset_id=None):
    role = (await db_session.exec(select(UserRole))).one()
    role.role = ProjectRole.ASSET_WORKER
    db_session.add(role)
    if asset_id is not None:
        db_session.add(RawMaterialUserAsset(user_id=role.user_id, asset_id=asset_id))
    await db_session.commit()


async def test_worker_empty_assets_cannot_receive_or_lease_any_row(db_session):
    row = (await db_session.exec(select(MaterialMovement))).first()
    policy = access_for(db_session)
    key = f"rm_material_movements/{row.id}/demandPlanTons"
    await restrict_admin_to_asset(db_session)
    async with policy.session_factory() as session:
        permissions = await policy.resolve(session)
        assert await policy.cells(session, permissions, [key], write=True) == set()
    assert (
        await policy.filter_messages(
            [{"type": "cell:changed", "rowId": str(row.id), "field": "demandPlanTons", "value": 42}]
        )
        == []
    )


async def test_worker_only_receives_own_asset_cells_and_selection(db_session):
    rows = list(
        (
            await db_session.exec(
                select(MaterialMovement).where(col(MaterialMovement.report_month) == date(2026, 8, 1))
            )
        ).all()
    )
    arm = await db_session.get(AssetRawMaterial, rows[0].asset_raw_material_id)
    own = rows[0]
    foreign = (
        await db_session.exec(
            select(MaterialMovement)
            .join(AssetRawMaterial)
            .where(
                col(MaterialMovement.report_month) == date(2026, 8, 1),
                col(AssetRawMaterial.asset_id) != arm.asset_id,
            )
        )
    ).first()
    policy = access_for(db_session)
    await restrict_admin_to_asset(db_session, arm.asset_id)
    own_key = f"rm_material_movements/{own.id}/demandPlanTons"
    foreign_key = f"rm_material_movements/{foreign.id}/demandPlanTons"
    messages = [
        {"type": "cell:changed", "rowId": str(own.id), "field": "demandPlanTons", "value": 12},
        {"type": "cell:locked", "key": foreign_key},
        {
            "type": "presence:update",
            "members": {
                "worker": {
                    "role": "asset_worker",
                    "assets": ["foreign"],
                    "selection": {"label": "secret", "cellKeys": [foreign_key]},
                }
            },
        },
    ]
    filtered = await policy.filter_messages(messages)
    assert [item["type"] for item in filtered] == ["cell:changed", "presence:update"]
    assert "worker" not in filtered[1]["members"]
    async with policy.session_factory() as session:
        permissions = await policy.resolve(session)
        assert await policy.cells(session, permissions, [own_key, foreign_key], write=True) == {own_key}


async def test_central_admin_reads_all_without_explicit_grants_and_rechecks_revocation(db_session):
    row = (await db_session.exec(select(MaterialMovement))).first()
    policy = access_for(db_session)
    message = {"type": "cell:changed", "rowId": str(row.id), "field": "demandPlanTons", "value": 42}
    assert await policy.filter_messages([message]) == [message]
    await restrict_admin_to_asset(db_session)
    assert await policy.filter_messages([message]) == []


@pytest.mark.parametrize(
    ("section", "month"),
    [("renovationPlan", None), ("materialMovement", "2026-08-02"), ("rawMaterialPrices", "2026-08-01")],
)
async def test_invalid_room_shape_is_rejected(db_session, section, month):
    with pytest.raises(RoomAccessDeniedError):
        access_for(db_session, section=section, month=month)


async def test_socket_cannot_choose_another_project(db_session, store):
    socket = FakeWebSocket(project="renovation")
    await raw_materials_ws(cast("WebSocket", socket), db_session)
    assert socket.closed_code == 4403
    assert not socket.accepted


async def test_lease_rejects_unknown_rows_and_read_only_fields(db_session, store):
    row = (await db_session.exec(select(MaterialMovement))).first()
    policy = access_for(db_session)
    socket = FakeWebSocket(project="raw_materials")
    await _handle_message(
        {"type": "lease:acquire", "cells": [f"rm_material_movements/{row.id}/businessPlanTons"]},
        websocket=cast("WebSocket", socket),
        store=store,
        room="room",
        project_key="raw_materials",
        owner={"userId": "admin"},
        owned_leases=set(),
        access=policy,
    )
    assert socket.sent[-1]["type"] == "error"


async def test_fifty_idle_sockets_release_database_connections(db_session, test_engine, store):
    await db_session.close()
    sockets = [FakeWebSocket(project="raw_materials") for _ in range(50)]
    sessions = [AsyncSession(test_engine, expire_on_commit=False) for _ in sockets]
    tasks = [
        asyncio.create_task(raw_materials_ws(cast("WebSocket", socket), session))
        for socket, session in zip(sockets, sessions, strict=True)
    ]
    try:
        await _wait_until(
            lambda: all(any(message.get("type") == "hello" for message in socket.sent) for socket in sockets),
            attempts=1000,
        )
        await _wait_until(lambda: all(not session.in_transaction() for session in sessions))
        await _wait_until(lambda: test_engine.pool.checkedout() == 0)
    finally:
        for socket in sockets:
            socket.disconnect = True
        await asyncio.gather(*tasks)
        for session in sessions:
            await session.close()


async def test_relay_failure_closes_socket_instead_of_silently_stalling():
    class FailedStore:
        async def subscribe(self, _room, **_kwargs: object) -> AsyncIterator[dict]:
            error = "subscription unavailable"
            raise RuntimeError(error)
            yield {}  # pragma: no cover

    socket = FakeWebSocket(project="raw_materials")
    manager = RoomManager(cast("CollabStore", FailedStore()))
    try:
        with pytest.raises(Exception, match="Room subscription failed"):
            await manager.join("room", cast("WebSocket", socket), {})
        await _wait_until(lambda: socket.closed_code == 1013)
    finally:
        await manager.leave("room", cast("WebSocket", socket))
