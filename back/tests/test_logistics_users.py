"""The «Логистика» users dictionary: roles, asset grants and membership management."""

from types import SimpleNamespace
from uuid import UUID

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.logistics.constants import LOGISTICS_PROJECT_KEY

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
REVIEWER_HEADERS = signed_identity_headers(email="reviewer@example.ru", name="Reviewer User")
WORKER_HEADERS = signed_identity_headers(email="worker@example.ru", name="Worker User")

ORG_BSK = "00000000-0000-0000-0000-000000000101"
ORG_SODA = "00000000-0000-0000-0000-000000000102"
COUNTRY_RU = "00000000-0000-0000-0000-000000000201"
COUNTRY_DE = "00000000-0000-0000-0000-000000000203"
TRANSPORT_AUTO = "00000000-0000-0000-0000-000000000301"
VEHICLE_TENT = "00000000-0000-0000-0000-000000000401"
PERIOD = "2026-11-01"


async def _project_id(session: AsyncSession) -> UUID:
    return (await session.exec(select(Project.id).where(col(Project.key) == LOGISTICS_PROJECT_KEY))).one()


async def _add_user(client: AsyncClient, email: str, role: str, assets: list[str] | None = None) -> dict:
    response = await client.post(
        "/api/logistics/users",
        headers=ADMIN_HEADERS,
        json={"email": email, "fullname": email, "role": role, "assetIds": assets or []},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_rate(client: AsyncClient, *, asset: str = ORG_BSK) -> dict:
    response = await client.post(
        "/api/logistics/rates",
        headers=ADMIN_HEADERS,
        params={"session_id": "users-test"},
        json={
            "periodDate": PERIOD,
            "assetId": asset,
            "hazardClassValue": None,
            "countryFromId": COUNTRY_RU,
            "countryToId": COUNTRY_DE,
            "transportTypeId": TRANSPORT_AUTO,
            "vehicleTypeId": VEHICLE_TENT,
            "loadingTons": 1,
            "freightRateRub": 10,
            "pprRateRub": 0,
            "deliveryRateRub": 0,
            "netRateRub": 10,
            "priceKind": "INDICATIVE",
            "note": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_users_create_patch_and_delete(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    # Local mode: the full name is editable (under Keycloak it belongs to the
    # corporate account — see the dedicated test below).
    monkeypatch.setattr("src.logistics.users.get_settings", lambda: SimpleNamespace(keycloak_enabled=False))
    created = await _add_user(client, "New.User@example.ru", "asset_worker", [ORG_BSK])
    # The address is normalized; the role decides the derived access flag.
    assert created["email"] == "new.user@example.ru"
    assert created["role"] == "asset_worker"
    assert created["isActive"] is True
    assert created["assetIds"] == [ORG_BSK]
    assert created["assetNames"] == ["БСК"]

    listed = (await client.get("/api/logistics/users", headers=ADMIN_HEADERS)).json()
    assert any(row["id"] == created["id"] for row in listed)

    patched = await client.patch(
        f"/api/logistics/users/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"fullname": "Новое Имя", "role": "reviewer"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["fullname"] == "Новое Имя"
    assert patched.json()["role"] == "reviewer"
    # A non-worker role never keeps asset grants.
    assert patched.json()["assetIds"] == []

    blocked = await client.patch(
        f"/api/logistics/users/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"role": "blocked"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["isActive"] is False
    deleted = await client.delete(f"/api/logistics/users/{created['id']}", headers=ADMIN_HEADERS)
    assert deleted.status_code == 204
    listed = (await client.get("/api/logistics/users", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != created["id"] for row in listed)


async def test_a_new_member_is_blocked_by_default(client: AsyncClient) -> None:
    response = await client.post(
        "/api/logistics/users",
        headers=ADMIN_HEADERS,
        json={"email": "blocked@example.ru", "fullname": "Заблокированный"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "blocked"
    assert response.json()["isActive"] is False


async def test_duplicate_user_is_rejected(client: AsyncClient) -> None:
    await _add_user(client, "dup@example.ru", "reviewer")
    second = await client.post(
        "/api/logistics/users",
        headers=ADMIN_HEADERS,
        json={"email": "dup@example.ru", "fullname": "Дубль"},
    )
    assert second.status_code == 409
    assert "уже добавлен" in second.json()["detail"]


async def test_keycloak_owns_the_full_name(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr("src.logistics.users.get_settings", lambda: SimpleNamespace(keycloak_enabled=True))
    created = await _add_user(client, "keycloak@example.ru", "reviewer")
    user_id = created["id"]
    rejected = await client.patch(
        f"/api/logistics/users/{user_id}",
        headers=ADMIN_HEADERS,
        json={"fullname": "Другое Имя"},
    )
    assert rejected.status_code == 409
    assert "корпоративной" in rejected.json()["detail"]
    # The role is still ours to manage.
    allowed = await client.patch(
        f"/api/logistics/users/{user_id}",
        headers=ADMIN_HEADERS,
        json={"role": "blocked"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["isActive"] is False


async def test_only_a_blocked_member_can_be_deleted_and_nobody_deletes_themselves(
    client: AsyncClient,
) -> None:
    created = await _add_user(client, "active@example.ru", "reviewer")
    refused = await client.delete(f"/api/logistics/users/{created['id']}", headers=ADMIN_HEADERS)
    assert refused.status_code == 409
    assert "заблокируйте" in refused.json()["detail"]

    listed = (await client.get("/api/logistics/users", headers=ADMIN_HEADERS)).json()
    me = next(row for row in listed if row["email"] == "admin@example.ru")
    self_patch = await client.patch(f"/api/logistics/users/{me['id']}", headers=ADMIN_HEADERS, json={"fullname": "Я"})
    assert self_patch.status_code == 409
    self_delete = await client.delete(f"/api/logistics/users/{me['id']}", headers=ADMIN_HEADERS)
    assert self_delete.status_code == 409


async def test_a_working_role_is_active_and_the_blocked_one_is_not(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _add_user(client, "member@example.ru", "admin")
    worker = await _add_user(client, "member.worker@example.ru", "asset_worker", [ORG_BSK, ORG_SODA])
    project_id = await _project_id(db_session)
    for email, role, active, assets in (
        ("member@example.ru", ProjectRole.ADMIN, True, 0),
        ("member.worker@example.ru", ProjectRole.ASSET_WORKER, True, 2),
    ):
        person = (await db_session.exec(select(User).where(col(User.email) == email))).one()
        link = await db_session.get(UserRole, (person.id, project_id))
        assert link is not None
        assert link.role == role
        assert link.is_active is active
        from src.logistics.models import LogisticsUserAsset

        grants = (
            await db_session.exec(select(LogisticsUserAsset).where(col(LogisticsUserAsset.user_id) == person.id))
        ).all()
        assert len(grants) == assets
    assert admin["role"] == "admin"
    assert worker["assetIds"] == [ORG_BSK, ORG_SODA]


async def test_a_reviewer_reads_the_whole_table_but_never_writes(client: AsyncClient) -> None:
    await _create_rate(client, asset=ORG_BSK)
    await _add_user(client, "reviewer@example.ru", "reviewer")
    view = await client.get("/api/logistics/rates", headers=REVIEWER_HEADERS, params={"month": 11, "year": 2026})
    assert view.status_code == 200, view.text
    assert len(view.json()["rows"]) == 1
    write = await client.post(
        "/api/logistics/rates",
        headers=REVIEWER_HEADERS,
        json={
            "periodDate": PERIOD,
            "assetId": ORG_BSK,
            "hazardClassValue": None,
            "countryFromId": COUNTRY_RU,
            "countryToId": COUNTRY_DE,
            "transportTypeId": TRANSPORT_AUTO,
            "vehicleTypeId": VEHICLE_TENT,
            "loadingTons": 1,
            "freightRateRub": 1,
            "pprRateRub": 0,
            "deliveryRateRub": 0,
            "netRateRub": 1,
            "priceKind": "INDICATIVE",
            "note": None,
        },
    )
    assert write.status_code == 403
    # The dictionary tabs are not shown to a reviewer, but the rates form still
    # resolves its labels: the reference reads are open to every member.
    assert (await client.get("/api/logistics/dictionaries/countries", headers=REVIEWER_HEADERS)).status_code == 200
    assert (await client.get("/api/logistics/users", headers=REVIEWER_HEADERS)).status_code == 403


async def test_an_asset_worker_sees_and_edits_only_their_asset(client: AsyncClient) -> None:
    own = await _create_rate(client, asset=ORG_BSK)
    foreign = await _create_rate(client, asset=ORG_SODA)
    await _add_user(client, "worker@example.ru", "asset_worker", [ORG_BSK])

    view = await client.get("/api/logistics/rates", headers=WORKER_HEADERS, params={"month": 11, "year": 2026})
    assert view.status_code == 200, view.text
    assert [row["id"] for row in view.json()["rows"]] == [own["id"]]

    foreign_patch = await client.patch(
        f"/api/logistics/rates/{foreign['id']}",
        headers=WORKER_HEADERS,
        params={"session_id": "worker-test"},
        json={"field": "note", "value": "чужое"},
    )
    assert foreign_patch.status_code == 404
    own_patch = await client.patch(
        f"/api/logistics/rates/{own['id']}",
        headers=WORKER_HEADERS,
        params={"session_id": "worker-test"},
        json={"field": "note", "value": "своё"},
    )
    assert own_patch.status_code == 200, own_patch.text
    assert own_patch.json()["note"] == "своё"

    foreign_create = await client.post(
        "/api/logistics/rates",
        headers=WORKER_HEADERS,
        json={
            "periodDate": PERIOD,
            "assetId": ORG_SODA,
            "hazardClassValue": None,
            "countryFromId": COUNTRY_RU,
            "countryToId": COUNTRY_DE,
            "transportTypeId": TRANSPORT_AUTO,
            "vehicleTypeId": VEHICLE_TENT,
            "loadingTons": 1,
            "freightRateRub": 1,
            "pprRateRub": 0,
            "deliveryRateRub": 0,
            "netRateRub": 1,
            "priceKind": "INDICATIVE",
            "note": None,
        },
    )
    assert foreign_create.status_code == 403
    # Deleting is the administrator's call.
    assert (
        await client.delete(
            f"/api/logistics/rates/{own['id']}", headers=WORKER_HEADERS, params={"session_id": "worker-test"}
        )
    ).status_code == 403


async def test_an_asset_worker_without_grants_is_blocked(client: AsyncClient) -> None:
    await _add_user(client, "worker@example.ru", "asset_worker", [])
    response = await client.get("/api/logistics/rates", headers=WORKER_HEADERS, params={"month": 11, "year": 2026})
    assert response.status_code == 403


async def test_a_blocked_member_has_no_access(client: AsyncClient) -> None:
    await client.post(
        "/api/logistics/users",
        headers=ADMIN_HEADERS,
        json={"email": "blocked@example.ru", "fullname": "Заблокирован"},
    )
    headers = signed_identity_headers(email="blocked@example.ru", name="Заблокирован")
    assert (
        await client.get("/api/logistics/rates", headers=headers, params={"month": 11, "year": 2026})
    ).status_code == 403
    assert (await client.get("/api/logistics/dictionaries/orgs", headers=headers)).status_code == 403
