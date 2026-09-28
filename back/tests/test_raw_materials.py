"""«Сырьё» form scenario: access and reading «Движение» and «Цены»."""

from conftest import signed_identity_headers
from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import ProjectRole, User, UserRole
from src.auth.service import activate_project_user, grant_project_assets, grant_project_role
from src.models import Project
from src.renovation.constants import RENOVATION_PROJECT_KEY

# Edit endpoints require the collab browser-session id (undo scoping);
# tests that don't exercise undo use one fixed session.
TEST_SESSION_ID = "test-session"


def admin_headers() -> dict[str, str]:
    return {
        "email": "admin@example.ru",
        "preferred_username": "admin",
        "subject": "admin-sub",
        "email_verified": "true",
    }


async def test_admin_reads_meta(client) -> None:
    response = await client.get("/api/raw-materials/meta", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    body = response.json()
    assert [asset["name"] for asset in body["assets"]] == ["БСЗ", "ВОС", "СНХЗ"]
    assert all(asset["id"] for asset in body["assets"])
    assert body["movementPeriods"] == ["2026-08-01", "2026-09-01"]
    assert body["deliveryBases"] == ["FCA", "FOB"]


async def test_admin_reads_movement_rows(client) -> None:
    response = await client.get(
        "/api/raw-materials/movement",
        params={"report_month": "2026-08-01"},
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 4

    snhz_ammiak = next(row for row in rows if row["assetName"] == "СНХЗ" and row["rawMaterialName"] == "Аммиак")
    assert snhz_ammiak["rawMaterialName"] == "Аммиак"
    assert snhz_ammiak["businessPlanTons"] == 100.0
    assert snhz_ammiak["demandPlanTons"] == 100.0
    assert snhz_ammiak["consumptionFactTons"] == 80.0
    assert snhz_ammiak["demandDeviationTons"] == 20.0
    assert snhz_ammiak["deliveryPlanTons"] == 90.0
    assert snhz_ammiak["deliveryFactTons"] == 70.0
    assert snhz_ammiak["deliveryDeviationTons"] == 20.0
    assert snhz_ammiak["balanceStartTons"] == 10.0
    assert snhz_ammiak["storageStandardTons"] == 50.0
    assert snhz_ammiak["deviationReason"] == "Недопоставка"
    assert snhz_ammiak["updatedBy"] == ""


async def test_movement_without_period_uses_latest(client) -> None:
    response = await client.get("/api/raw-materials/movement", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 4
    assert all(row["demandPlanTons"] is None for row in rows)


async def test_admin_reads_prices(client) -> None:
    response = await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 4
    row = next(row for row in rows if row["assetName"] == "СНХЗ" and row["rawMaterialName"] == "Аммиак")
    assert row["fact2025"] == 1.0
    assert row["businessPlanQuantityTons"] == 100.0
    assert row["businessPlanUsdPerTon"] == 200.0
    assert row["businessPlanRubPerTon"] == 20000.0
    assert row["deliveryCostRub"] == 500.0
    assert row["deliveryBasisName"] is None


async def test_worker_sees_only_bound_assets(client, db_session) -> None:
    headers = {
        "email": "worker@example.ru",
        "preferred_username": "worker",
        "subject": "worker-sub",
        "email_verified": "true",
    }
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ", "ВОС"])
    await db_session.commit()

    meta = (await client.get("/api/raw-materials/meta", headers=signed_identity_headers(**headers))).json()
    assert [asset["name"] for asset in meta["assets"]] == ["ВОС", "СНХЗ"]
    assert all(asset["id"] for asset in meta["assets"])

    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**headers),
        )
    ).json()
    assert {row["assetName"] for row in rows} == {"СНХЗ", "ВОС"}

    prices = (await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**headers))).json()
    assert {row["assetName"] for row in prices} == {"ВОС", "СНХЗ"}


async def test_inactive_user_has_no_form_access(client, db_session) -> None:
    headers = {
        "email": "inactive@example.ru",
        "preferred_username": "inactive",
        "subject": "inactive-sub",
        "email_verified": "true",
    }
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "inactive@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await db_session.commit()

    response = await client.get("/api/raw-materials/meta", headers=signed_identity_headers(**headers))
    assert response.status_code == 403


async def test_admin_reads_raw_materials_dictionary(client) -> None:
    response = await client.get("/api/raw-materials/raw-materials", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    rows = response.json()
    assert [row["name"] for row in rows] == ["Аммиак", "Метанол"]
    by_name = {row["name"]: row["assetNames"] for row in rows}
    assert by_name["Аммиак"] == ["БСЗ", "СНХЗ"]
    assert by_name["Метанол"] == ["ВОС", "СНХЗ"]
    assert all(row["updatedBy"] == "" for row in rows)


async def test_admin_reads_assets_dictionary(client) -> None:
    response = await client.get("/api/raw-materials/assets", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["БСЗ", "ВОС", "СНХЗ"]


async def test_admin_reads_delivery_bases_dictionary(client) -> None:
    response = await client.get(
        "/api/raw-materials/delivery-bases",
        headers=signed_identity_headers(**admin_headers()),
    )

    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["FCA", "FOB"]


async def test_admin_reads_users_dictionary(client) -> None:
    # The login (upsert) stamps last_authenticated_at; the admin row then carries it.
    login = await client.get("/api/me", headers=signed_identity_headers(**admin_headers()))
    assert login.status_code == 200

    response = await client.get("/api/raw-materials/users", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    rows = response.json()
    assert any(row["email"] == "admin@example.ru" for row in rows)
    admin = next(row for row in rows if row["email"] == "admin@example.ru")
    assert admin["fullname"] == "Admin User"
    assert admin["isAdmin"] is True
    assert admin["isActive"] is True
    assert admin["assetNames"] == []
    assert admin["lastAuthenticatedAt"] is not None
    assert all(row["email"] for row in rows)


async def test_worker_cannot_read_dictionaries(client, db_session) -> None:
    headers = {
        "email": "worker@example.ru",
        "preferred_username": "worker",
        "subject": "worker-sub",
        "email_verified": "true",
    }
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ"])
    await db_session.commit()

    for path in ["raw-materials", "assets", "delivery-bases", "users"]:
        response = await client.get(
            f"/api/raw-materials/{path}",
            headers=signed_identity_headers(**headers),
        )
        assert response.status_code == 403, path


async def test_admin_updates_movement_cells(client) -> None:
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()
    row_id = rows[0]["id"]

    response = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": row_id, "field": "consumptionFactTons", "value": 95.5}],
        },
    )

    assert response.status_code == 200
    assert response.json() == 1
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()
    assert rows[0]["consumptionFactTons"] == 95.5
    assert rows[0]["updatedBy"] == "Admin User"


async def test_admin_updates_price_cells(client) -> None:
    rows = (await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**admin_headers()))).json()
    row_id = rows[0]["id"]

    response = await client.patch(
        "/api/raw-materials/prices",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"updates": [{"rowId": row_id, "field": "fact2025", "value": 42.0}]},
    )

    assert response.status_code == 200
    rows = (await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**admin_headers()))).json()
    updated = next(row for row in rows if row["id"] == row_id)
    assert updated["fact2025"] == 42.0


async def test_movement_cell_unknown_field_rejected(client) -> None:
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**admin_headers()),
        )
    ).json()

    response = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": rows[0]["id"], "field": "bogusField", "value": 1}],
        },
    )

    assert response.status_code == 422


async def test_admin_crud_raw_material(client) -> None:
    created = await client.post(
        "/api/raw-materials/raw-materials",
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Этилен", "assetIds": []},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["name"] == "Этилен"
    assert row["assetNames"] == []
    assert row["updatedBy"] == "Admin User"

    updated = await client.patch(
        f"/api/raw-materials/raw-materials/{row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Этилен-гликоль"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Этилен-гликоль"

    deleted = await client.delete(
        f"/api/raw-materials/raw-materials/{row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 204
    rows = (
        await client.get("/api/raw-materials/raw-materials", headers=signed_identity_headers(**admin_headers()))
    ).json()
    assert all(item["id"] != row["id"] for item in rows)


async def test_added_asset_creates_price_and_movement_rows(client) -> None:
    """A new asset on a raw-material item immediately yields «Цены» and «Движение» rows."""
    headers = signed_identity_headers(**admin_headers())
    meta = (await client.get("/api/raw-materials/meta", headers=headers)).json()
    assets = {asset["name"]: asset["id"] for asset in meta["assets"]}

    positions = (await client.get("/api/raw-materials/raw-materials", headers=headers)).json()
    ammiak = next(position for position in positions if position["name"] == "Аммиак")
    # Аммиак is initially linked to БСЗ and СНХЗ — adding ВОС.
    response = await client.patch(
        f"/api/raw-materials/raw-materials/{ammiak['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={"assetIds": [assets["БСЗ"], assets["СНХЗ"], assets["ВОС"]]},
    )
    assert response.status_code == 200
    assert response.json()["assetNames"] == ["БСЗ", "ВОС", "СНХЗ"]

    prices = (await client.get("/api/raw-materials/prices", headers=headers)).json()
    new_price = next(row for row in prices if row["rawMaterialName"] == "Аммиак" and row["assetName"] == "ВОС")
    assert new_price["deliveryBasisName"] == "FCA"  # first basis alphabetically — like in the seed
    assert new_price["businessPlanQuantityTons"] is None

    for report_month in meta["movementPeriods"]:
        rows = (
            await client.get(
                "/api/raw-materials/movement",
                params={"report_month": report_month},
                headers=headers,
            )
        ).json()
        new_row = next(row for row in rows if row["rawMaterialName"] == "Аммиак" and row["assetName"] == "ВОС")
        assert new_row["businessPlanTons"] is None
        assert new_row["demandPlanTons"] is None


async def test_removing_added_asset_requires_empty_rows(client) -> None:
    """An asset with empty rows can be dropped; with filled data — 409."""
    headers = signed_identity_headers(**admin_headers())
    meta = (await client.get("/api/raw-materials/meta", headers=headers)).json()
    assets = {asset["name"]: asset["id"] for asset in meta["assets"]}

    positions = (await client.get("/api/raw-materials/raw-materials", headers=headers)).json()
    ammiak = next(position for position in positions if position["name"] == "Аммиак")

    # Add ВОС to Аммиак and fill a «Движение» cell for August.
    added = await client.patch(
        f"/api/raw-materials/raw-materials/{ammiak['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={"assetIds": [assets["БСЗ"], assets["СНХЗ"], assets["ВОС"]]},
    )
    assert added.status_code == 200
    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=headers,
        )
    ).json()
    new_row = next(row for row in rows if row["assetName"] == "ВОС" and row["rawMaterialName"] == "Аммиак")

    # Fill a «Движение» cell for August.
    filled = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": new_row["id"], "field": "balanceOnSiteTons", "value": 12.5}],
        },
    )
    assert filled.status_code == 200

    # An asset with a filled row can't be dropped — 409.
    blocked = await client.patch(
        f"/api/raw-materials/raw-materials/{ammiak['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={"assetIds": [assets["БСЗ"], assets["СНХЗ"]]},
    )
    assert blocked.status_code == 409
    assert "ВОС" in blocked.json()["detail"]

    # Clear the data — now it can be dropped; «Движение»/«Цены» rows are deleted.
    cleared = await client.patch(
        "/api/raw-materials/movement",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={
            "reportMonth": "2026-08-01",
            "updates": [{"rowId": new_row["id"], "field": "balanceOnSiteTons", "value": None}],
        },
    )
    assert cleared.status_code == 200
    removed = await client.patch(
        f"/api/raw-materials/raw-materials/{ammiak['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={"assetIds": [assets["БСЗ"], assets["СНХЗ"]]},
    )
    assert removed.status_code == 200
    prices = (await client.get("/api/raw-materials/prices", headers=headers)).json()
    assert not any(row["rawMaterialName"] == "Аммиак" and row["assetName"] == "ВОС" for row in prices)


async def test_admin_crud_asset_and_basis(client) -> None:
    created = await client.post(
        "/api/raw-materials/assets",
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "КХЗ"},
    )
    assert created.status_code == 201
    asset = created.json()
    assert asset["name"] == "КХЗ"
    assert asset["updatedBy"] == "Admin User"

    deleted = await client.delete(
        f"/api/raw-materials/assets/{asset['id']}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 204

    basis = await client.post(
        "/api/raw-materials/delivery-bases",
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "EXW"},
    )
    assert basis.status_code == 201
    basis_row = basis.json()
    assert basis_row["name"] == "EXW"

    renamed = await client.patch(
        f"/api/raw-materials/delivery-bases/{basis_row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "EXW-склад"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "EXW-склад"

    deleted_basis = await client.delete(
        f"/api/raw-materials/delivery-bases/{basis_row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted_basis.status_code == 204


async def test_admin_batch_dictionary_is_one_undo_action(client) -> None:
    """One batch request edits several dictionary rows and undoes as one action."""
    headers = signed_identity_headers(**admin_headers())
    ids = []
    for name in ("БАЗА-A", "БАЗА-B"):
        created = await client.post("/api/raw-materials/delivery-bases", headers=headers, json={"name": name})
        ids.append(created.json()["id"])

    response = await client.patch(
        "/api/raw-materials/dictionary",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={
            "section": "deliveryBases",
            "updates": [
                {"rowId": ids[0], "field": "name", "value": "БАЗА-A2"},
                {"rowId": ids[1], "field": "name", "value": "БАЗА-B2"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["applied"] == 2

    rows = (await client.get("/api/raw-materials/delivery-bases", headers=headers)).json()
    by_id = {row["id"]: row for row in rows}
    assert by_id[ids[0]]["name"] == "БАЗА-A2"
    assert by_id[ids[1]]["name"] == "БАЗА-B2"

    # A single undo reverts both rows.
    undone = await client.post(
        "/api/raw-materials/undo",
        params={"session_id": TEST_SESSION_ID},
        headers=headers,
        json={"section": "deliveryBases"},
    )
    assert undone.status_code == 200
    assert undone.json()["applied"] is True
    rows = (await client.get("/api/raw-materials/delivery-bases", headers=headers)).json()
    by_id = {row["id"]: row for row in rows}
    assert by_id[ids[0]]["name"] == "БАЗА-A"
    assert by_id[ids[1]]["name"] == "БАЗА-B"


async def test_admin_crud_project_user(client) -> None:
    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "petrov@example.ru", "fullname": "Петров Пётр", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["fullname"] == "Петров Пётр"
    assert row["isAdmin"] is False
    assert row["assetNames"] == ["СНХЗ"]

    updated = await client.patch(
        f"/api/raw-materials/users/{row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
        json={"assetNames": ["ВОС", "СНХЗ"]},
    )
    assert updated.status_code == 200
    assert updated.json()["assetNames"] == ["ВОС", "СНХЗ"]

    deleted = await client.delete(
        f"/api/raw-materials/users/{row['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 409
    assert "Активен" in deleted.json()["detail"]

    listing = await client.get(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert listing.status_code == 200
    assert any(user["id"] == row["id"] for user in listing.json())


async def test_create_user_is_provisioned_to_all_projects(client, db_session) -> None:
    """A new user appears in all projects."""
    db_session.add(Project(key="kppk", name="КППК"))
    await db_session.commit()

    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "sidorov@example.ru", "fullname": "Сидоров Сидор", "isAdmin": True, "assetNames": []},
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    raw_project = (await db_session.exec(select(Project).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))).one()
    other_project = (await db_session.exec(select(Project).where(col(Project.key) == "kppk"))).one()
    renovation_project = (
        await db_session.exec(select(Project).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one()
    links = (await db_session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).all()
    assert {link.project_id for link in links} == {raw_project.id, other_project.id, renovation_project.id}
    by_project = {link.project_id: link for link in links}
    assert by_project[raw_project.id].is_active is True
    assert by_project[raw_project.id].role == ProjectRole.ADMIN
    assert by_project[other_project.id].is_active is False
    assert by_project[other_project.id].role == ProjectRole.ASSET_WORKER
    assert by_project[renovation_project.id].is_active is False


async def test_create_user_normalizes_email_to_lowercase(client, db_session) -> None:
    """Email is stored lowercased; a duplicate in another case is rejected."""
    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "Smirnov@Example.RU", "fullname": "Смирнов С. С.", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["email"] == "smirnov@example.ru"

    stored = (await db_session.exec(select(User).where(col(User.email) == "smirnov@example.ru"))).one()
    assert stored.email == "smirnov@example.ru"

    again = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "SMIRNOV@example.ru", "fullname": "Смирнов С. С.", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert again.status_code == 409
    assert "уже существует" in again.json()["detail"]

    invalid = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "не-адрес", "fullname": "Икс И. И.", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert invalid.status_code == 422


async def test_keycloak_login_finds_user_case_insensitively(client, db_session) -> None:
    """Keycloak with a differently-cased email finds the existing user."""
    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "kuznetsov@example.ru", "fullname": "Кузнецов К.", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    response = await client.get(
        "/api/me",
        headers=signed_identity_headers(
            email="Kuznetsov@Example.RU",
            name="Кузнецов Иван Петрович",
            preferred_username="kuznetsov",
            email_verified="true",
        ),
    )
    assert response.status_code == 200
    assert response.json()["email"] == "kuznetsov@example.ru"

    users = (await db_session.exec(select(User).where(col(User.email) == "kuznetsov@example.ru"))).all()
    assert len(users) == 1
    assert str(users[0].id) == user_id
    assert users[0].name == "Кузнецов Иван Петрович"


async def test_create_user_for_existing_email_adds_missing_project_link(client, db_session) -> None:
    """An existing user without a project link gets only the link."""
    existing = User(
        email="petrov@example.ru",
        name="Петров Пётр",
    )
    db_session.add(existing)
    await db_session.commit()

    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "petrov@example.ru", "fullname": "Другое Имя", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["id"] == str(existing.id)
    assert row["fullname"] == "Петров Пётр"
    assert row["isActive"] is True

    duplicates = (await db_session.exec(select(User).where(col(User.email) == "petrov@example.ru"))).all()
    assert len(duplicates) == 1

    # Adding the same user to the project again — a clear error.
    again = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "petrov@example.ru", "fullname": "Петров Пётр", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert again.status_code == 409
    assert "уже существует" in again.json()["detail"]


async def test_keycloak_login_overwrites_name_and_keeps_membership(client, db_session) -> None:
    """After a Keycloak login the FIO is overwritten, access is kept."""
    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={
            "email": "kuznetsov@example.ru",
            "fullname": "Кузнецов Кузьма",
            "isAdmin": False,
            "assetNames": ["СНХЗ"],
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    response = await client.get(
        "/api/me",
        headers=signed_identity_headers(
            email="kuznetsov@example.ru",
            name="Кузнецов Иван Петрович",
            preferred_username="kuznetsov",
            subject="kuznetsov-sub",
            email_verified="true",
            issuer="http://keycloak:8080/realms/reportcollector",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "kuznetsov@example.ru"
    assert body["access"][0]["key"] == RAW_MATERIALS_PROJECT_KEY

    user = (await db_session.exec(select(User).where(col(User.id) == user_id))).one()
    assert user.name == "Кузнецов Иван Петрович"
    assert user.email == "kuznetsov@example.ru"
    raw_project = (await db_session.exec(select(Project).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))).one()
    link = (
        await db_session.exec(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id == raw_project.id)
        )
    ).one()
    assert link.is_active is True
    assert link.role == ProjectRole.ASSET_WORKER


async def test_worker_cannot_write_dictionaries(client, db_session) -> None:
    headers = {
        "email": "worker-write@example.ru",
        "preferred_username": "worker-write",
        "subject": "worker-write-sub",
        "email_verified": "true",
    }
    await client.post(
        "/api/me/projects/raw_materials/request-access",
        headers=signed_identity_headers(**headers),
    )
    user = (await db_session.exec(select(User).where(col(User.email) == "worker-write@example.ru"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ"])
    await db_session.commit()

    response = await client.post(
        "/api/raw-materials/raw-materials",
        headers=signed_identity_headers(**headers),
        json={"name": "Незваный", "assetIds": []},
    )
    assert response.status_code == 403
    response = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**headers),
        json={"email": "x@example.ru", "fullname": "X", "assetNames": []},
    )
    assert response.status_code == 403
