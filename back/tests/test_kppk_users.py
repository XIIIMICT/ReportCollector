"""KPPK user administration and project-scoped replay."""

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole

HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


async def test_create_update_delete_and_replay_kppk_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/users?session_id=kppk-users",
        headers=HEADERS,
        json={"email": "KPPK.New@example.ru", "fullname": "Новый пользователь"},
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["email"] == "kppk.new@example.ru"
    assert row["isAdmin"] is True
    assert row["orgNames"] == []
    user_id = row["id"]
    response = await client.patch(
        f"/api/kppk/users/{user_id}?session_id=kppk-users", headers=HEADERS, json={"isActive": False}
    )
    assert response.status_code == 200, response.text
    response = await client.post("/api/kppk/undo?session_id=kppk-users", headers=HEADERS, json={"section": "kppkUsers"})
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True, response.text
    rows = (await client.get("/api/kppk/users", headers=HEADERS)).json()
    assert next(item for item in rows if item["id"] == user_id)["isActive"] is True
    await client.patch(f"/api/kppk/users/{user_id}?session_id=kppk-users", headers=HEADERS, json={"isActive": False})
    response = await client.delete(f"/api/kppk/users/{user_id}?session_id=kppk-users", headers=HEADERS)
    assert response.status_code == 204, response.text
    response = await client.post("/api/kppk/undo?session_id=kppk-users", headers=HEADERS, json={"section": "kppkUsers"})
    assert response.json()["applied"] is True, response.text
    rows = (await client.get("/api/kppk/users", headers=HEADERS)).json()
    assert any(item["id"] == user_id for item in rows)


async def test_users_reject_other_roles_and_invalid_addresses(client: AsyncClient) -> None:
    for payload, expected in [
        ({"email": "other@example.ru", "fullname": "Имя", "isAdmin": False}, 409),
        ({"email": "bad-address", "fullname": "Имя"}, 422),
    ]:
        response = await client.post("/api/kppk/users?session_id=users", headers=HEADERS, json=payload)
        assert response.status_code == expected


async def test_users_requires_admin(client: AsyncClient) -> None:
    viewer = signed_identity_headers(email="viewer@example.ru", name="Viewer")
    response = await client.get("/api/kppk/users", headers=viewer)
    assert response.status_code == 403


async def test_kppk_user_role_change_preserves_other_project_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    projects = {item.key: item.id for item in (await db_session.exec(select(Project))).all()}
    person = User(email="scope@example.ru", name="Scoped User")
    db_session.add(person)
    await db_session.flush()
    db_session.add(
        UserRole(user_id=person.id, project_id=projects["raw_materials"], role=ProjectRole.ASSET_WORKER, is_active=True)
    )
    db_session.add(
        UserRole(user_id=person.id, project_id=projects["kppk"], role=ProjectRole.ASSET_WORKER, is_active=False)
    )
    await db_session.commit()
    raw_link = await db_session.get(UserRole, (person.id, projects["raw_materials"]))
    assert raw_link is not None
    original = raw_link.role, raw_link.is_active
    response = await client.patch(
        f"/api/kppk/users/{person.id}?session_id=role-scope", headers=HEADERS, json={"isAdmin": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["isAdmin"] is True
    response = await client.post("/api/kppk/undo?session_id=role-scope", headers=HEADERS, json={"section": "kppkUsers"})
    assert response.json()["applied"] is True, response.text
    await db_session.refresh(raw_link)
    assert (raw_link.role, raw_link.is_active) == original
    kppk_link = await db_session.get(UserRole, (person.id, projects["kppk"]), populate_existing=True)
    assert kppk_link is not None
    assert kppk_link.role == ProjectRole.ASSET_WORKER


async def test_kppk_users_rejects_org_grants_and_exports_empty_orgs(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/users?session_id=users",
        headers=HEADERS,
        json={"email": "new@example.ru", "fullname": "Имя", "orgNames": ["АО"]},
    )
    assert response.status_code == 422
    response = await client.post(
        "/api/kppk/export",
        headers=HEADERS,
        json={
            "section": "kppkUsers",
            "fileName": "Пользователи.xlsx",
            "columns": [
                {"key": "fullname", "headerPath": ["ФИО"], "width": 30},
                {"key": "orgNames", "headerPath": ["ДЗО"], "width": 20},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK"
