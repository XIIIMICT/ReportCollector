"""Сценарий форм проекта «Сырьё»: доступ и чтение «Движения» и «Цен»."""

from conftest import signed_identity_headers
from sqlmodel import col, select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import ProjectRole, User
from src.auth.service import activate_project_user, grant_project_assets, grant_project_role


def admin_headers() -> dict[str, str]:
    return {
        "email": "admin@example.local",
        "preferred_username": "admin",
        "subject": "admin-sub",
        "email_verified": "true",
    }


async def test_admin_reads_meta(client) -> None:
    response = await client.get("/api/raw-materials/meta", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    body = response.json()
    assert body["assets"] == ["БСЗ", "ВОС", "СНХЗ"]
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
    assert len(rows) == 1
    assert [row["assetName"] for row in rows] == ["СНХЗ"]

    snhz_ammiak = rows[0]
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
        "email": "worker@example.local",
        "preferred_username": "worker",
        "subject": "worker-sub",
        "email_verified": "true",
    }
    await client.get("/api/me", headers=signed_identity_headers(**headers))
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.local"))).one()
    await grant_project_role(db_session, user.id, RAW_MATERIALS_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, RAW_MATERIALS_PROJECT_KEY)
    await grant_project_assets(db_session, user.id, ["СНХЗ", "ВОС"])
    await db_session.commit()

    meta = (await client.get("/api/raw-materials/meta", headers=signed_identity_headers(**headers))).json()
    assert meta["assets"] == ["ВОС", "СНХЗ"]

    rows = (
        await client.get(
            "/api/raw-materials/movement",
            params={"report_month": "2026-08-01"},
            headers=signed_identity_headers(**headers),
        )
    ).json()
    assert [row["assetName"] for row in rows] == ["СНХЗ"]

    prices = (await client.get("/api/raw-materials/prices", headers=signed_identity_headers(**headers))).json()
    assert {row["assetName"] for row in prices} == {"ВОС", "СНХЗ"}


async def test_inactive_user_has_no_form_access(client, db_session) -> None:
    headers = {
        "email": "inactive@example.local",
        "preferred_username": "inactive",
        "subject": "inactive-sub",
        "email_verified": "true",
    }
    await client.get("/api/me", headers=signed_identity_headers(**headers))
    user = (await db_session.exec(select(User).where(col(User.email) == "inactive@example.local"))).one()
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
    response = await client.get("/api/raw-materials/users", headers=signed_identity_headers(**admin_headers()))

    assert response.status_code == 200
    rows = response.json()
    assert any(row["email"] == "admin@example.local" for row in rows)
    admin = next(row for row in rows if row["email"] == "admin@example.local")
    assert admin["fullname"] == "Admin User"
    assert admin["isAdmin"] is True
    assert admin["isActive"] is True
    assert admin["assetNames"] == []
    assert all(row["email"] for row in rows)


async def test_worker_cannot_read_dictionaries(client, db_session) -> None:
    headers = {
        "email": "worker@example.local",
        "preferred_username": "worker",
        "subject": "worker-sub",
        "email_verified": "true",
    }
    await client.get("/api/me", headers=signed_identity_headers(**headers))
    user = (await db_session.exec(select(User).where(col(User.email) == "worker@example.local"))).one()
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
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "Этилен-гликоль"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Этилен-гликоль"

    deleted = await client.delete(
        f"/api/raw-materials/raw-materials/{row['id']}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 204
    rows = (
        await client.get("/api/raw-materials/raw-materials", headers=signed_identity_headers(**admin_headers()))
    ).json()
    assert all(item["id"] != row["id"] for item in rows)


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
        headers=signed_identity_headers(**admin_headers()),
        json={"name": "EXW-склад"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "EXW-склад"

    deleted_basis = await client.delete(
        f"/api/raw-materials/delivery-bases/{basis_row['id']}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted_basis.status_code == 204


async def test_admin_crud_project_user(client) -> None:
    created = await client.post(
        "/api/raw-materials/users",
        headers=signed_identity_headers(**admin_headers()),
        json={"email": "petrov@example.local", "fullname": "Петров Пётр", "isAdmin": False, "assetNames": ["СНХЗ"]},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["fullname"] == "Петров Пётр"
    assert row["isAdmin"] is False
    assert row["assetNames"] == ["СНХЗ"]

    updated = await client.patch(
        f"/api/raw-materials/users/{row['id']}",
        headers=signed_identity_headers(**admin_headers()),
        json={"assetNames": ["ВОС", "СНХЗ"]},
    )
    assert updated.status_code == 200
    assert updated.json()["assetNames"] == ["ВОС", "СНХЗ"]

    deleted = await client.delete(
        f"/api/raw-materials/users/{row['id']}",
        headers=signed_identity_headers(**admin_headers()),
    )
    assert deleted.status_code == 204


async def test_worker_cannot_write_dictionaries(client, db_session) -> None:
    headers = {
        "email": "worker-write@example.local",
        "preferred_username": "worker-write",
        "subject": "worker-write-sub",
        "email_verified": "true",
    }
    await client.get("/api/me", headers=signed_identity_headers(**headers))
    user = (await db_session.exec(select(User).where(col(User.email) == "worker-write@example.local"))).one()
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
        json={"email": "x@example.local", "fullname": "X", "assetNames": []},
    )
    assert response.status_code == 403
