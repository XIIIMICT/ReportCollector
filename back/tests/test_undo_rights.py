"""Undo follows the CURRENT write rights, not the rights at edit time.

A user who may no longer write a form must not be able to undo a journaled
action there (undo is a write). The check lives in `collab/undo.py`
(`_authorize_action`) and mirrors the routers' rules.
"""

from datetime import UTC, datetime

from conftest import signed_identity_headers
from httpx import AsyncClient
from sqlmodel import select

from src.auth.models import Project
from src.renovation.models import RProjectRole, RUserRole

MEMBER_HEADERS = signed_identity_headers(email="ren-check@example.ru", name="Ren Checker")


async def _member_with_role(db_session, role: RProjectRole) -> None:
    from src.auth.models import User

    user = (await db_session.exec(select(User).where(User.email == "ren-check@example.ru"))).first()
    now = datetime.now(UTC)
    if user is None:
        user = User(email="ren-check@example.ru", name="Ren Checker")
        db_session.add(user)
        await db_session.flush()
    project_id = (await db_session.exec(select(Project.id).where(Project.key == "renovation"))).one()
    db_session.add(
        RUserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
            updater_user_id=None,
        )
    )
    await db_session.commit()


async def test_approver_cannot_undo_a_plan_row_change(client: AsyncClient, db_session) -> None:
    # The approver may read the plan but not write it — so an undo is refused
    # even when the action is in the caller's own stack.
    await _member_with_role(db_session, RProjectRole.APPROVER)
    created = await client.post(
        "/api/renovation/plan/rows",
        headers=MEMBER_HEADERS,
        params={"session_id": "approver"},
        json={},
    )
    # The approver cannot even add a row; the undo stack stays empty.
    assert created.status_code == 403
    state = await client.get(
        "/api/renovation/undo-state",
        headers=MEMBER_HEADERS,
        params={"session_id": "approver", "section": "renovationPlan"},
    )
    assert state.json() == {"canUndo": False, "canRedo": False}
    undone = await client.post(
        "/api/renovation/undo",
        headers=MEMBER_HEADERS,
        params={"session_id": "approver"},
        json={"section": "renovationPlan"},
    )
    assert undone.status_code == 200
    assert undone.json()["applied"] is False
