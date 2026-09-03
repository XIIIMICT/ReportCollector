"""Access scenario: project URLs, self-requested membership, admin activation.

The flow fixed in the spec:
1. The root URL never provisions anything: an unknown (or not-yet-activated)
   user gets an empty access list and no project links are created.
2. Opening a specific project's URL creates the user record (when missing)
   and a single inactive link to that project — the project admin sees the
   person in «Пользователи» and can grant access.
3. After the project admin enables is_active, the user gets access to
   exactly that project (other projects stay closed).
"""

from conftest import signed_identity_headers
from sqlalchemy import update
from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import ProjectRole, User, UserRole
from src.auth.service import activate_project_user, grant_project_role
from src.models import Project
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RUserRole

NEW_USER_EMAIL = "new_user@example.ru"
NEW_USER_SUB = "new-user-sub"
AUTH_HEADERS = {
    "email": NEW_USER_EMAIL,
    "preferred_username": "new_user",
    "subject": NEW_USER_SUB,
    "issuer": "http://keycloak:8080/realms/reportcollector",
    "email_verified": "true",
}


async def test_root_does_not_provision_unknown_user(client, db_session) -> None:
    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == NEW_USER_EMAIL
    assert body["access"] == []

    # The root URL must not create the user or any project membership.
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one_or_none()
    assert user is None


async def test_project_url_provisions_inactive_membership(client, db_session) -> None:
    response = await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    assert response.status_code == 200
    assert response.json()["hasAccess"] is False

    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    assert user.oidc_sub == NEW_USER_SUB

    # Exactly one project link: inactive, awaiting the admin's activation.
    project = (await db_session.exec(select(Project).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))).one()
    links = (await db_session.exec(select(UserRole).where(col(UserRole.user_id) == user.id))).all()
    assert len(links) == 1
    assert links[0].project_id == project.id
    assert links[0].is_active is False
    assert links[0].role == ProjectRole.ASSET_WORKER


async def test_request_access_is_idempotent(client, db_session) -> None:
    for _ in range(2):
        response = await client.post(
            "/api/me/projects/raw_materials/request-access",
            headers=signed_identity_headers(**AUTH_HEADERS),
        )
        assert response.status_code == 200
        assert response.json()["hasAccess"] is False

    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    links = (await db_session.exec(select(UserRole).where(col(UserRole.user_id) == user.id))).all()
    assert len(links) == 1


async def test_request_access_unknown_project_404(client) -> None:
    response = await client.post(
        "/api/me/projects/nope/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    assert response.status_code == 404


async def test_renovation_request_access_creates_renovation_link(client, db_session) -> None:
    project = (await db_session.exec(select(Project).where(col(Project.key) == RENOVATION_PROJECT_KEY))).one()

    response = await client.post(
        "/api/me/projects/renovation/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    assert response.status_code == 200
    assert response.json()["hasAccess"] is False

    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    # The renovation «Пользователи» dictionary lists users by r_user_roles:
    # the self-request creates that link too (inactive, block unset).
    r_links = (await db_session.exec(select(RUserRole).where(col(RUserRole.user_id) == user.id))).all()
    assert len(r_links) == 1
    assert r_links[0].project_id == project.id
    assert r_links[0].is_active is False
    assert r_links[0].block is None


async def test_inactive_user_has_no_project_access(client) -> None:
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 200
    assert response.json()["access"] == []


async def test_admin_role_sees_all_assets(client, db_session) -> None:
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()

    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ADMIN)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 200
    body = response.json()
    project_access = next(a for a in body["access"] if a["key"] == RAW_MATERIALS_PROJECT_KEY)
    assert project_access["roles"] == [ProjectRole.ADMIN.value]
    assert project_access["assets"] == [
        {"name": "БСЗ"},
        {"name": "ВОС"},
        {"name": "СНХЗ"},
    ]


async def test_asset_worker_sees_only_bound_assets(client, db_session) -> None:
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()

    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    from src.auth.service import grant_project_assets

    await grant_project_assets(db_session, user.id, ["СНХЗ", "БСЗ"])

    body = (await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))).json()
    project_access = next(a for a in body["access"] if a["key"] == RAW_MATERIALS_PROJECT_KEY)
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
    project_access = next(a for a in body["access"] if a["key"] == RAW_MATERIALS_PROJECT_KEY)
    assert project_access["roles"] == [ProjectRole.ADMIN.value]


async def test_invalid_signature_is_rejected(client) -> None:
    headers = signed_identity_headers(**AUTH_HEADERS)
    headers["X-ReportCollector-Identity-Signature"] = "deadbeef"

    response = await client.get("/api/me", headers=headers)
    assert response.status_code == 401


async def test_deleted_user_is_blocked(client, db_session) -> None:
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    await db_session.exec(update(User).where(col(User.id) == user.id).values(is_deleted=True))
    await db_session.commit()

    response = await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))
    assert response.status_code == 403


async def test_renovation_grant_makes_project_usable(client, db_session) -> None:
    """Enabling a renovation dictionary user unlocks the renovation workspace.

    The reported bug: a «Сырьё» member opened the renovation URL (inactive links
    were created), the admin then granted them access in the renovation
    «Пользователи» dictionary — but only the r_user_roles row changed. Access to
    /api/me is gated on the cross-project auth UserRole link, which stayed
    inactive, so the user kept only «Сырьё» and the root URL redirected there.
    """
    admin_headers = signed_identity_headers(
        email="admin@example.ru",
        preferred_username="admin",
        subject="admin-sub",
        email_verified="true",
    )

    # 1. The user is already an active «Сырьё» member.
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == NEW_USER_EMAIL))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)

    # 2. The same user opens the renovation URL -> renovation links are inactive.
    open_resp = await client.post(
        "/api/me/projects/renovation/request-access",
        headers=signed_identity_headers(**AUTH_HEADERS),
    )
    assert open_resp.status_code == 200
    assert open_resp.json()["hasAccess"] is False

    before = (await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))).json()
    assert {project["key"] for project in before["access"]} == {RAW_MATERIALS_PROJECT_KEY}

    # 3. The admin grants renovation access through the dictionary row.
    patch = await client.patch(
        f"/api/renovation/dictionary/users/{user.id}",
        headers=admin_headers,
        params={"session_id": "grant-test"},
        json={"role": "asset_worker", "block": "tashpaev", "isActive": True},
    )
    assert patch.status_code == 200
    assert patch.json()["isActive"] is True

    # 4. The user now has usable renovation access (and kept «Сырьё»).
    after = (await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))).json()
    keys = {project["key"] for project in after["access"]}
    assert RENOVATION_PROJECT_KEY in keys
    assert RAW_MATERIALS_PROJECT_KEY in keys

    # 5. Revoking it removes renovation from the usable access again.
    revoke = await client.patch(
        f"/api/renovation/dictionary/users/{user.id}",
        headers=admin_headers,
        params={"session_id": "revoke-test"},
        json={"isActive": False},
    )
    assert revoke.status_code == 200
    revoked = (await client.get("/api/me", headers=signed_identity_headers(**AUTH_HEADERS))).json()
    assert {project["key"] for project in revoked["access"]} == {RAW_MATERIALS_PROJECT_KEY}
