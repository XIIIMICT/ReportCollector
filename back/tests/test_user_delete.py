"""Deleting a user from a project, in every project.

The project link (and its asset grants) goes; the account is soft-deleted once
no project link is left, so the person disappears from the «Пользователи»
dictionaries instead of lingering as an empty row. The deletion is undoable in
«Сырьё»/«Ремонт» (КППК has no undo for user links yet).
"""

from datetime import UTC, datetime
from uuid import UUID

from conftest import signed_identity_headers
from httpx import AsyncClient
from sqlmodel import select

from src.auth.models import Project, ProjectRole, User, UserRole

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


async def _make_user(db_session, email: str, *, projects: list[str], active: bool = True) -> User:
    user = User(email=email, name="Удаляемый Пользователь")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(UTC)
    for key in projects:
        project_id = (await db_session.exec(select(Project.id).where(Project.key == key))).one()
        db_session.add(
            UserRole(
                user_id=user.id,
                project_id=project_id,
                role=ProjectRole.ASSET_WORKER,
                is_active=active,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.commit()
    return user


async def test_raw_user_delete_removes_the_link_and_soft_deletes_the_account(client: AsyncClient, db_session) -> None:
    user = await _make_user(db_session, "gone-raw@example.ru", projects=["raw_materials"], active=False)
    response = await client.delete(
        f"/api/raw-materials/users/{user.id}",
        headers=ADMIN_HEADERS,
        params={"session_id": "users-session"},
    )
    assert response.status_code == 204, response.text

    link = await db_session.get(UserRole, (user.id, (await _project_id(db_session, "raw_materials"))))
    assert link is None
    account = await db_session.get(User, user.id)
    assert account is not None
    assert account.is_deleted is True

    rows = (await client.get("/api/raw-materials/users", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != str(user.id) for row in rows)


async def test_raw_user_delete_keeps_the_account_while_another_project_needs_it(
    client: AsyncClient, db_session
) -> None:
    user = await _make_user(
        db_session, "gone-partial@example.ru", projects=["raw_materials", "renovation"], active=False
    )
    response = await client.delete(f"/api/raw-materials/users/{user.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 204, response.text
    account = await db_session.get(User, user.id)
    assert account is not None
    assert account.is_deleted is False


async def test_raw_user_delete_is_undoable(client: AsyncClient, db_session) -> None:
    user = await _make_user(db_session, "undo-user@example.ru", projects=["raw_materials"], active=False)
    response = await client.delete(
        f"/api/raw-materials/users/{user.id}",
        headers=ADMIN_HEADERS,
        params={"session_id": "users-undo"},
    )
    assert response.status_code == 204

    undone = await client.post(
        "/api/raw-materials/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": "users-undo"},
        json={"section": "users"},
    )
    assert undone.status_code == 200
    assert undone.json()["applied"] is True
    account = await db_session.get(User, user.id)
    assert account is not None
    assert account.is_deleted is False
    link = await db_session.get(UserRole, (user.id, (await _project_id(db_session, "raw_materials"))))
    assert link is not None


async def test_user_with_access_cannot_be_deleted(client: AsyncClient, db_session) -> None:
    """The protective layer: revoke access first, then delete."""
    user = await _make_user(db_session, "active-user@example.ru", projects=["raw_materials"], active=True)
    user_id = user.id
    response = await client.delete(f"/api/raw-materials/users/{user_id}", headers=ADMIN_HEADERS)
    assert response.status_code == 409
    assert "Сначала снимите доступ" in response.json()["detail"]
    # The link survived.
    project_id = await _project_id(db_session, "raw_materials")
    assert await db_session.get(UserRole, (user_id, project_id)) is not None


async def test_renovation_user_delete(client: AsyncClient, db_session) -> None:
    from src.renovation.models import RProjectRole, RUserRole

    user = await _make_user(db_session, "gone-renovation@example.ru", projects=[])
    renovation_project = await _project_id(db_session, "renovation")
    db_session.add(
        RUserRole(
            user_id=user.id,
            project_id=renovation_project,
            role=RProjectRole.ADMIN,
            is_active=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            updated_by="",
        )
    )
    await db_session.commit()

    response = await client.delete(f"/api/renovation/dictionary/users/{user.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 204, response.text
    link = await db_session.get(RUserRole, (user.id, renovation_project))
    assert link is None


async def test_kppk_user_delete(client: AsyncClient, db_session) -> None:
    user = await _make_user(db_session, "gone-kppk@example.ru", projects=["kppk"], active=False)
    rows = (await client.get("/api/kppk/users", headers=ADMIN_HEADERS)).json()
    assert any(row["id"] == str(user.id) for row in rows)

    response = await client.delete(f"/api/kppk/users/{user.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 204, response.text
    rows = (await client.get("/api/kppk/users", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != str(user.id) for row in rows)


async def _project_id(db_session, key: str) -> UUID:
    return (await db_session.exec(select(Project.id).where(Project.key == key))).one()


async def test_admin_cannot_remove_themselves(client: AsyncClient, db_session) -> None:
    """Every project refuses to remove the caller from their own project."""
    me_id = (await db_session.exec(select(User.id).where(User.email == "admin@example.ru"))).one()
    for path in (
        f"/api/raw-materials/users/{me_id}",
        f"/api/renovation/dictionary/users/{me_id}",
        f"/api/kppk/users/{me_id}",
    ):
        response = await client.delete(path, headers=ADMIN_HEADERS)
        assert response.status_code == 409, (path, response.text)
        assert "себя" in response.json()["detail"]

    # Nothing was touched: every link the admin had is still there.
    for key in ("raw_materials", "renovation", "kppk"):
        project_id = await _project_id(db_session, key)
        link = await db_session.get(UserRole, (me_id, project_id))
        if link is not None:
            continue
        # КППК/«Ремонт» may keep their own link table — check that too.
        from src.renovation.models import RUserRole

        assert await db_session.get(RUserRole, (me_id, project_id)) is not None, key
