"""Access scenario: a new user → admin activation → access.

Checks the flow fixed in the spec:
1. On a Keycloak login, an unknown user appears for all admins of all
   projects: a link with each project is created with is_active=false.
2. After the project admin enables is_active, the user gets access to
   exactly that project (other projects stay closed).
"""

from conftest import signed_identity_headers
from sqlalchemy import update
from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import ProjectRole, User, UserRole
from src.auth.service import activate_project_user, grant_project_role
from src.models import Project

NEW_USER_EMAIL = "new_user@example.ru"
NEW_USER_SUB = "new-user-sub"
AUTH_HEADERS = {
    "email": NEW_USER_EMAIL,
    "preferred_username": "new_user",
    "subject": NEW_USER_SUB,
    "issuer": "http://keycloak:8080/realms/reportcollector",
    "email_verified": "true",
}


async def test_new_user_is_created_with_inactive_memberships(client, db_session) -> None:
    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == NEW_USER_EMAIL
    assert body["access"] == []

    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one_or_none()
    assert user is not None
    assert user.oidc_sub == NEW_USER_SUB
    assert user.preferred_username == "new_user"

    # The project link was created by provisioning but is inactive — the user
    # is visible in the project dictionary and awaits admin activation.
    links = (await db_session.exec(select(UserRole).where(col(UserRole.user_id) == user.id))).all()
    assert links, "Провижининг должен создать связи со всеми проектами"
    assert all(not link.is_active for link in links)
    assert all(link.role == ProjectRole.ASSET_WORKER for link in links)


async def test_inactive_user_has_no_project_access(client, db_session) -> None:
    await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))

    active_project = (
        await db_session.exec(select(Project).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).one_or_none()
    assert active_project is not None

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 200
    assert response.json()["access"] == []


async def test_admin_role_sees_all_assets(client, db_session) -> None:
    await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()

    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ADMIN)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 200
    body = response.json()
    project_access = body["access"][0]
    assert project_access["key"] == RAW_MATERIALS_PROJECT_KEY
    assert project_access["roles"] == [ProjectRole.ADMIN.value]
    assert project_access["assets"] == [
        {"name": "БСЗ"},
        {"name": "ВОС"},
        {"name": "СНХЗ"},
    ]


async def test_asset_worker_sees_only_bound_assets(client, db_session) -> None:
    await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()

    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    from src.auth.service import grant_project_assets

    await grant_project_assets(db_session, user.id, ["СНХЗ", "БСЗ"])

    body = (await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))).json()
    project_access = body["access"][0]
    assert project_access["roles"] == [ProjectRole.ASSET_WORKER.value]
    assert project_access["assets"] == [
        {"name": "БСЗ"},
        {"name": "СНХЗ"},
    ]


async def test_seed_admin_is_active_with_admin_role(client) -> None:
    headers = {
        "email": "admin@example.ru",
        "preferred_username": "admin",
        "subject": "admin-sub",
        "email_verified": "true",
    }
    response = await client.get("/api/me", headers=signed_identity_headers(**headers))

    assert response.status_code == 200
    body = response.json()
    project_access = body["access"][0]
    assert project_access["key"] == RAW_MATERIALS_PROJECT_KEY
    assert project_access["roles"] == [ProjectRole.ADMIN.value]


async def test_invalid_signature_is_rejected(client) -> None:
    headers = signed_identity_headers(**AUTH_HEADERS)
    headers["X-ReportCollector-Identity-Signature"] = "deadbeef"

    response = await client.get("/api/me", headers=headers)
    assert response.status_code == 401


async def test_deleted_user_is_blocked(client, db_session) -> None:
    await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    await db_session.exec(update(User).where(col(User.id) == user.id).values(is_deleted=True))
    await db_session.commit()

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 403
