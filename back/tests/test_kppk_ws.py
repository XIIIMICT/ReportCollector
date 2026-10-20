"""WebSocket room of the «КППК» dictionaries: access and room shape."""

from datetime import UTC, date, datetime
from typing import cast

import pytest
from conftest import signed_identity_headers
from fastapi import WebSocket
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_collab import FakeWebSocket

from src.auth.models import ProjectRole, User
from src.auth.service import verify_signed_identity
from src.collab.access import KPPK_SECTIONS, RoomAccess, RoomAccessDeniedError
from src.collab.events import room_key
from src.collab.ws import _resolve_kppk_access, kppk_ws
from src.kppk.models import KppkCost, KppkOrg


async def _member(db_session, *, active: bool = True) -> User:
    """Create a plain КППК member (a project link without the admin role)."""
    from src.auth.models import Project, UserRole

    user = User(email="member@example.ru", name="Member User")
    db_session.add(user)
    await db_session.flush()
    project_id = (await db_session.exec(select(Project.id).where(Project.key == "kppk"))).one()
    db_session.add(
        UserRole(
            user_id=user.id,
            project_id=project_id,
            role=ProjectRole.ASSET_WORKER,
            is_active=active,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return user


def _access(
    db_session,
    *,
    section: str,
    email: str = "admin@example.ru",
    report_month: str | None = None,
    scope: str | None = None,
) -> RoomAccess:
    headers = signed_identity_headers(email=email, name="Kppk User")
    return RoomAccess(
        lambda: AsyncSession(db_session.bind, expire_on_commit=False),
        verify_signed_identity(headers),
        _resolve_kppk_access,
        "kppk",
        section,
        report_month,
        scope,
    )


def test_kppk_declares_its_sections() -> None:
    assert set(KPPK_SECTIONS) == {
        "kppkPositions",
        "kppkRaws",
        "kppkOrgs",
        "kppkRealizationPlan",
        "kppkRealizationFact",
        "kppkCost",
        "kppkUsers",
    }
    assert KPPK_SECTIONS["kppkOrgs"][0] is KppkOrg


async def test_admin_joins_every_kppk_room(db_session) -> None:
    org_id = (await db_session.exec(select(KppkOrg.id))).first()
    for section in KPPK_SECTIONS:
        report_month = "2026-06-01" if section in {"kppkRealizationPlan", "kppkRealizationFact", "kppkCost"} else None
        scope = str(org_id) if report_month is not None else None
        policy = _access(db_session, section=section, report_month=report_month, scope=scope)
        async with policy.session_factory() as session:
            user, _roles, assets, block = await policy.resolve(session)
        assert user.email == "admin@example.ru"
        assert assets == []
        assert block is None


async def test_member_is_denied_the_admin_only_kppk_rooms(db_session) -> None:
    await _member(db_session)
    policy = _access(db_session, section="kppkOrgs", email="member@example.ru")
    async with policy.session_factory() as session:
        with pytest.raises(RoomAccessDeniedError):
            await policy.resolve(session)


async def test_unknown_kppk_section_is_rejected(db_session) -> None:
    with pytest.raises(RoomAccessDeniedError):
        _access(db_session, section="kppkPeriods")


async def test_kppk_socket_rejects_another_project(db_session, store) -> None:
    socket = FakeWebSocket(project="raw_materials")
    await kppk_ws(cast("WebSocket", socket), db_session)
    assert socket.closed_code == 4403
    assert not socket.accepted


async def test_kppk_cells_are_isolated_by_org_and_month(db_session) -> None:
    orgs = list((await db_session.exec(select(KppkOrg))).all())
    rows = [
        KppkCost(org_id=orgs[0].id, period_date=date(2026, 6, 1), product="Scoped product"),
        KppkCost(org_id=orgs[1].id, period_date=date(2026, 6, 1), product="Scoped product"),
        KppkCost(org_id=orgs[0].id, period_date=date(2026, 7, 1), product="Scoped product"),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    policy = _access(db_session, section="kppkCost", report_month="2026-06-01", scope=str(orgs[0].id))
    keys = [f"kppk_cost/{row.id}/fullCostBp" for row in rows]
    async with policy.session_factory() as session:
        access = await policy.resolve(session)
        assert await policy.cells(session, access, keys, write=False) == {keys[0]}
        assert await policy.cells(session, access, keys, write=True) == {keys[0]}
    assert room_key("kppk", "kppkCost", "2026-06-01", str(orgs[0].id)) != room_key(
        "kppk", "kppkCost", "2026-06-01", str(orgs[1].id)
    )


@pytest.mark.parametrize("scope", [None, "not-a-uuid"])
def test_kppk_form_requires_org_scope(db_session, scope) -> None:
    with pytest.raises(RoomAccessDeniedError):
        _access(db_session, section="kppkCost", report_month="2026-06-01", scope=scope)
