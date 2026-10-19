"""WebSocket room of the «КППК» dictionaries: access and room shape."""

from datetime import UTC, datetime
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
from src.collab.ws import _resolve_kppk_access, kppk_ws
from src.kppk.models import KppkOrg


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


def _access(db_session, *, section: str, email: str = "admin@example.ru") -> RoomAccess:
    headers = signed_identity_headers(email=email, name="Kppk User")
    return RoomAccess(
        lambda: AsyncSession(db_session.bind, expire_on_commit=False),
        verify_signed_identity(headers),
        _resolve_kppk_access,
        "kppk",
        section,
        None,
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
    for section in KPPK_SECTIONS:
        policy = _access(db_session, section=section)
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
