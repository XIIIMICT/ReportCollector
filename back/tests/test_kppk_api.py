"""Tests for the «КППК» dictionary phase: source copies, product positions, CRUD."""

from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from conftest import signed_identity_headers
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole, User
from src.auth.service import activate_project_user, grant_project_role
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.models import KppkOrg, KppkOrgProduct, KppkProduct

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")


async def _org_by_name(session: AsyncSession, name: str) -> KppkOrg:
    return (await session.exec(select(KppkOrg).where(col(KppkOrg.name) == name))).one()


async def _product_by_name(session: AsyncSession, name: str) -> KppkProduct:
    return (await session.exec(select(KppkProduct).where(col(KppkProduct.name) == name))).one()


# ─── Access ────────────────────────────────────────────────────────────────


async def test_endpoints_require_kppk_access(client: AsyncClient) -> None:
    """A user without a kppk role gets 403 on every dictionary read."""
    for path in ("/api/kppk/product-positions", "/api/kppk/raws", "/api/kppk/orgs", "/api/kppk/periods"):
        response = await client.get(path, headers=VIEWER_HEADERS)
        assert response.status_code == 403


async def test_admin_reads_all_dictionaries(client: AsyncClient) -> None:
    response = await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 3
    # The default order is the source number; the unnumbered product is last.
    assert [row["name"] for row in rows] == [
        "Сульфат натрия",
        "Сода кальцинированная марки Б",
        "Сода кальцинированная марки А",
    ]
    assert [row["productNum"] for row in rows] == [1, 2, None]
    by_product = {row["name"]: row for row in rows}
    assert by_product["Сульфат натрия"]["orgNames"] == ["АО «СНХЗ»"]
    # A product without links is still a row (its ДЗО cell is empty).
    assert by_product["Сода кальцинированная марки А"]["orgNames"] == []

    response = await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    orgs = response.json()
    assert {org["code"] for org in orgs} == {"БСЗ", "СНХЗ"}
    # Sorted by name («Березниковский…» leads «СНХЗ»); the source «№» is not exposed.
    assert [org["code"] for org in orgs] == ["БСЗ", "СНХЗ"]

    response = await client.get("/api/kppk/periods", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    periods = response.json()
    assert [period["monthName"] for period in periods] == ["Июнь", "Июль"]

    response = await client.get("/api/kppk/raws", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    raws = response.json()
    assert [row["name"] for row in raws] == ["Каучук", "Резина"]
    assert [row["rawNum"] for row in raws] == [1, 2]


async def test_viewer_can_read_but_not_write(client: AsyncClient) -> None:
    response = await client.get("/api/kppk/raws", headers=VIEWER_HEADERS)
    assert response.status_code == 403

    response = await client.post("/api/kppk/raws", headers=VIEWER_HEADERS, json={"name": "Вода"})
    assert response.status_code == 403


# ─── Продуктовые позиции ──────────────────────────────────────────────────


async def test_product_position_create_updates_and_deletes(client: AsyncClient, db_session: AsyncSession) -> None:
    org = await _org_by_name(db_session, "АО «СНХЗ»")
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Каучук СКИ-3", "orgNames": [org.name]},
    )
    assert response.status_code == 201
    created = response.json()["row"]
    assert created["name"] == "Каучук СКИ-3"
    assert created["orgNames"] == ["АО «СНХЗ»"]
    assert created["productNum"] is None

    # The new product went into the product dictionary without a source num.
    product = await _product_by_name(db_session, "Каучук СКИ-3")
    assert product.product_num is None

    # A case-insensitively duplicate product is rejected.
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "каучук ски-3", "orgNames": []},
    )
    assert response.status_code == 400

    # An unknown ДЗО is rejected (the product itself is not created).
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Каучук СКИ-5", "orgNames": ["АО «Неизвестное»"]},
    )
    assert response.status_code == 400

    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "name", "value": "Каучук СКИ-5"},
    )
    assert response.status_code == 200
    assert response.json()["row"]["name"] == "Каучук СКИ-5"

    # orgNames replaces the whole ДЗО set of the product.
    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": ["АО «Березниковский содовый завод»"]},
    )
    assert response.status_code == 200
    assert response.json()["row"]["orgNames"] == ["АО «Березниковский содовый завод»"]

    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": []},
    )
    assert response.status_code == 200
    assert response.json()["row"]["orgNames"] == []

    # An unknown ДЗО is rejected on the cell edit as well.
    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": ["АО «Неизвестное»"]},
    )
    assert response.status_code == 400

    response = await client.delete(f"/api/kppk/product-positions/{created['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204
    remaining = (await db_session.exec(select(KppkOrgProduct))).all()
    assert len(remaining) == 2
    deleted = (await db_session.exec(select(KppkProduct).where(col(KppkProduct.name) == "Каучук СКИ-5"))).first()
    assert deleted is None


# ─── Сырьевые позиции ─────────────────────────────────────────────────────


async def test_raw_create_rename_delete(client: AsyncClient) -> None:
    response = await client.post("/api/kppk/raws", headers=ADMIN_HEADERS, json={"name": "Вода производственная"})
    assert response.status_code == 201
    created = response.json()["row"]
    assert created["rawNum"] is None

    response = await client.post("/api/kppk/raws", headers=ADMIN_HEADERS, json={"name": "воДА Производственная"})
    assert response.status_code == 400

    response = await client.patch(
        f"/api/kppk/raws/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "name", "value": "Вода"},
    )
    assert response.status_code == 200
    assert response.json()["row"]["name"] == "Вода"

    response = await client.delete(f"/api/kppk/raws/{created['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204


async def test_source_numbers_are_editable_and_unique(client: AsyncClient) -> None:
    """The «№» of products and raws stays editable — and unique."""
    product_rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS)).json()
    unnumbered = next(row for row in product_rows if row["productNum"] is None)

    # An occupied number («Сода ... Б» holds 2) is rejected with a clear message.
    response = await client.patch(
        f"/api/kppk/product-positions/{unnumbered['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": 2},
    )
    assert response.status_code == 400
    assert "уже используется" in response.json()["detail"]

    # A free number takes effect; an empty value clears it back to NULL.
    response = await client.patch(
        f"/api/kppk/product-positions/{unnumbered['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": 30},
    )
    assert response.status_code == 200
    assert response.json()["row"]["productNum"] == 30
    response = await client.patch(
        f"/api/kppk/product-positions/{unnumbered['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": None},
    )
    assert response.status_code == 200
    assert response.json()["row"]["productNum"] is None

    # A negative or fractional value is not a number.
    response = await client.patch(
        f"/api/kppk/product-positions/{unnumbered['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": -3},
    )
    assert response.status_code == 400

    # A product can take its number at creation; a taken one is rejected.
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Сода кальцинированная марки В", "orgNames": [], "productNum": 30},
    )
    assert response.status_code == 201
    assert response.json()["row"]["productNum"] == 30
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Сода кальцинированная марки Г", "orgNames": [], "productNum": 30},
    )
    assert response.status_code == 400

    # Raws follow the same rules; «Каучук» holds number 1, 8 is free.
    created = (
        await client.post(
            "/api/kppk/raws",
            headers=ADMIN_HEADERS,
            json={"name": "Кислота соляная", "rawNum": 7},
        )
    ).json()["row"]
    assert created["rawNum"] == 7
    response = await client.patch(
        f"/api/kppk/raws/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "rawNum", "value": 1},
    )
    assert response.status_code == 400
    response = await client.patch(
        f"/api/kppk/raws/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "rawNum", "value": 8},
    )
    assert response.status_code == 200
    assert response.json()["row"]["rawNum"] == 8


# ─── ДЗО ──────────────────────────────────────────────────────────────────


async def test_org_create_duplicate_and_delete(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/orgs",
        headers=ADMIN_HEADERS,
        json={"code": "ТЕСТ", "name": "АО «Тест»"},
    )
    assert response.status_code == 201
    created = response.json()["row"]

    response = await client.post(
        "/api/kppk/orgs",
        headers=ADMIN_HEADERS,
        json={"code": "ТЕСТ", "name": "АО «Другое»"},
    )
    assert response.status_code == 400

    response = await client.delete(f"/api/kppk/orgs/{created['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204


async def test_org_with_links_refuses_delete(client: AsyncClient, db_session: AsyncSession) -> None:
    org = await _org_by_name(db_session, "АО «СНХЗ»")
    response = await client.delete(f"/api/kppk/orgs/{org.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 400


# ─── Периоды ──────────────────────────────────────────────────────────────


async def test_period_create_patch_delete(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/periods",
        headers=ADMIN_HEADERS,
        json={
            "monthName": "Август",
            "yearNum": 2026,
            "firstDate": "2026-08-01",
            "lastDate": "2026-08-31",
        },
    )
    assert response.status_code == 201
    created = response.json()["row"]

    response = await client.post(
        "/api/kppk/periods",
        headers=ADMIN_HEADERS,
        json={
            "monthName": "август",
            "yearNum": 2026,
            "firstDate": "2026-08-01",
            "lastDate": "2026-08-31",
        },
    )
    assert response.status_code == 400

    response = await client.patch(
        f"/api/kppk/periods/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "lastDate", "value": "2026-07-01"},
    )
    assert response.status_code == 400

    response = await client.patch(
        f"/api/kppk/periods/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "firstDate", "value": "2026-08-02"},
    )
    assert response.status_code == 200

    response = await client.delete(f"/api/kppk/periods/{created['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204


async def test_unknown_patch_field_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/orgs",
        headers=ADMIN_HEADERS,
        json={"code": "ИНВ", "name": "АО «Инвентарь»"},
    )
    org_id = response.json()["row"]["id"]
    response = await client.patch(
        f"/api/kppk/orgs/{org_id}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": 1},
    )
    assert response.status_code == 400


async def test_unknown_row_404(client: AsyncClient) -> None:
    response = await client.delete(f"/api/kppk/raws/{uuid4()}", headers=ADMIN_HEADERS)
    assert response.status_code == 404


# ─── Undo/redo (the shared collaboration mechanism) ───────────────────────

TEST_SESSION_ID = "kppk-test-session"


async def _make_kppk_member(db_session: AsyncSession) -> User:
    """Create a «КППК» member with a non-admin role (for permission tests)."""
    now = datetime.now(UTC)
    user = User(email="kppk-member@example.ru", name="Member User", created_at=now, updated_at=now)
    db_session.add(user)
    await db_session.flush()
    await grant_project_role(db_session, user.id, KPPK_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, KPPK_PROJECT_KEY)
    await db_session.commit()
    return user


async def _undo(client: AsyncClient, section: str, direction: str = "undo") -> dict:
    response = await client.post(
        f"/api/kppk/{direction}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
        json={"section": section},
    )
    assert response.status_code == 200
    return response.json()


async def _undo_state(client: AsyncClient, section: str) -> dict:
    response = await client.get(
        "/api/kppk/undo-state",
        params={"session_id": TEST_SESSION_ID, "section": section},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    return response.json()


async def test_undo_redo_cell_edit(client: AsyncClient) -> None:
    """A cell edit is one action of the tab's scope; undo/redo replays it."""
    created = (
        await client.post(
            "/api/kppk/raws",
            params={"session_id": TEST_SESSION_ID},
            headers=ADMIN_HEADERS,
            json={"name": "Сера техническая"},
        )
    ).json()["row"]

    response = await client.patch(
        f"/api/kppk/raws/{created['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
        json={"field": "name", "value": "Сера комовая"},
    )
    assert response.status_code == 200

    assert await _undo_state(client, "kppkRaws") == {"canUndo": True, "canRedo": False}

    undone = await _undo(client, "kppkRaws")
    assert undone["applied"] is True
    assert undone["field"] == "name"
    assert undone["value"] == "Сера техническая"
    rows = (await client.get("/api/kppk/raws", headers=ADMIN_HEADERS)).json()
    assert next(row for row in rows if row["id"] == created["id"])["name"] == "Сера техническая"

    # The creation below the rename is still undoable.
    assert await _undo_state(client, "kppkRaws") == {"canUndo": True, "canRedo": True}

    redone = await _undo(client, "kppkRaws", direction="redo")
    assert redone["applied"] is True
    assert redone["value"] == "Сера комовая"
    rows = (await client.get("/api/kppk/raws", headers=ADMIN_HEADERS)).json()
    assert next(row for row in rows if row["id"] == created["id"])["name"] == "Сера комовая"


async def test_undo_history_is_per_tab_and_session(client: AsyncClient) -> None:
    """The history lives on the tab id and the page-lifetime session id."""
    created = (
        await client.post(
            "/api/kppk/raws",
            params={"session_id": TEST_SESSION_ID},
            headers=ADMIN_HEADERS,
            json={"name": "Сера гранулированная"},
        )
    ).json()["row"]

    # Another tab shares no history with «Сырьевые позиции».
    empty = await _undo(client, "kppkOrgs")
    assert empty["applied"] is False
    assert empty["reason"] == "empty"

    # A new page session (another session id) starts empty as well.
    response = await client.post(
        "/api/kppk/undo",
        params={"session_id": "another-page"},
        headers=ADMIN_HEADERS,
        json={"section": "kppkRaws"},
    )
    assert response.json()["reason"] == "empty"

    # The original session still sees and undoes the action.
    assert await _undo_state(client, "kppkRaws") == {"canUndo": True, "canRedo": False}
    undone = await _undo(client, "kppkRaws")
    assert undone["applied"] is True
    rows = (await client.get("/api/kppk/raws", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != created["id"] for row in rows)


async def test_undo_redo_create_restores_the_same_row(client: AsyncClient) -> None:
    """Undo of a creation removes the row; redo restores it under the same id."""
    created = (
        await client.post(
            "/api/kppk/orgs",
            params={"session_id": TEST_SESSION_ID},
            headers=ADMIN_HEADERS,
            json={"code": "УНД", "name": "АО «Ундо»"},
        )
    ).json()["row"]

    undone = await _undo(client, "kppkOrgs")
    assert undone["applied"] is True
    assert undone["field"] == "__created"
    rows = (await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != created["id"] for row in rows)

    redone = await _undo(client, "kppkOrgs", direction="redo")
    assert redone["applied"] is True
    rows = (await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)).json()
    restored = next(row for row in rows if row["id"] == created["id"])
    assert restored["name"] == "АО «Ундо»"


async def test_undo_redo_delete_brings_the_row_back(client: AsyncClient) -> None:
    """Undo of a deletion restores from the snapshot; redo deletes again."""
    created = (
        await client.post(
            "/api/kppk/orgs",
            params={"session_id": TEST_SESSION_ID},
            headers=ADMIN_HEADERS,
            json={"code": "ДЕЛ", "name": "АО «Дело»"},
        )
    ).json()["row"]
    response = await client.delete(
        f"/api/kppk/orgs/{created['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 204

    undone = await _undo(client, "kppkOrgs")
    assert undone["applied"] is True
    assert undone["field"] == "__deleted"
    rows = (await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)).json()
    assert next(row for row in rows if row["id"] == created["id"])["code"] == "ДЕЛ"

    redone = await _undo(client, "kppkOrgs", direction="redo")
    assert redone["applied"] is True
    rows = (await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)).json()
    assert all(row["id"] != created["id"] for row in rows)


async def test_undo_product_org_names_roundtrip(client: AsyncClient) -> None:
    """A product undo restores the whole ДЗО set from the snapshot."""
    rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS)).json()
    product = next(row for row in rows if row["name"] == "Сульфат натрия")
    assert product["orgNames"] == ["АО «СНХЗ»"]

    response = await client.patch(
        f"/api/kppk/product-positions/{product['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": []},
    )
    assert response.status_code == 200

    undone = await _undo(client, "kppkPositions")
    assert undone["applied"] is True
    assert undone["value"] == ["АО «СНХЗ»"]
    rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS)).json()
    assert next(row for row in rows if row["id"] == product["id"])["orgNames"] == ["АО «СНХЗ»"]


async def test_undo_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    """A project member without the admin role can neither undo nor redo."""
    await _make_kppk_member(db_session)
    member_headers = signed_identity_headers(email="kppk-member@example.ru", name="Member User")

    for direction in ("undo", "redo"):
        response = await client.post(
            f"/api/kppk/{direction}",
            params={"session_id": TEST_SESSION_ID},
            headers=member_headers,
            json={"section": "kppkRaws"},
        )
        assert response.status_code == 403

    # The member still reads the dictionaries and the (empty) undo state.
    response = await client.get(
        "/api/kppk/undo-state",
        params={"session_id": TEST_SESSION_ID, "section": "kppkRaws"},
        headers=member_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"canUndo": False, "canRedo": False}


# ─── Excel export ─────────────────────────────────────────────────────────


async def test_export_returns_workbook_and_reorders_rows(client: AsyncClient) -> None:
    orgs = (await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)).json()
    response = await client.post(
        "/api/kppk/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "kppkOrgs",
            "headerPinned": True,
            "columns": [
                {"key": "code", "headerPath": ["Код"], "width": 14},
                {"key": "name", "headerPath": ["Наименование"], "width": 38},
            ],
            "rowIds": [orgs[1]["id"], orgs[0]["id"]],
            "fileName": "ДЗО.xlsx",
        },
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert "ДЗО.xlsx" in unquote(response.headers["content-disposition"])
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["ДЗО"]
    assert sheet.cell(row=1, column=1).value == "Код"
    # «Видимая» export keeps the client row order (СНХЗ stands second by name).
    assert [sheet.cell(row=2, column=1).value, sheet.cell(row=3, column=1).value] == ["СНХЗ", "БСЗ"]


async def test_export_products_carry_the_orgs_dropdown(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "kppkPositions",
            "headerPinned": True,
            "columns": [
                {"key": "productNum", "headerPath": ["№"], "width": 9},
                {"key": "name", "headerPath": ["Продукт"], "width": 43},
                {"key": "orgNames", "headerPath": ["ДЗО"], "width": 36},
            ],
            "fileName": "Продуктовые позиции.xlsx",
        },
    )
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    assert workbook["Списки"].sheet_state == "hidden"
    # The dropdown of the ДЗО column holds every enterprise name.
    listed = [row[0] for row in workbook["Списки"].iter_rows(values_only=True)]
    assert listed == ["АО «Березниковский содовый завод»", "АО «СНХЗ»"]


async def test_export_rejects_hidden_columns(client: AsyncClient) -> None:
    response = await client.post(
        "/api/kppk/export",
        headers=ADMIN_HEADERS,
        json={
            "section": "kppkOrgs",
            "headerPinned": True,
            "columns": [{"key": "productNum", "headerPath": ["№"], "width": 9}],
            "fileName": "ДЗО.xlsx",
        },
    )
    assert response.status_code == 422


async def test_export_allowed_for_members(client: AsyncClient, db_session: AsyncSession) -> None:
    """Export reads what the member already sees — the role gate is read-level."""
    await _make_kppk_member(db_session)
    member_headers = signed_identity_headers(email="kppk-member@example.ru", name="Member User")
    response = await client.post(
        "/api/kppk/export",
        headers=member_headers,
        json={
            "section": "kppkRaws",
            "headerPinned": True,
            "columns": [{"key": "name", "headerPath": ["Сырьевая позиция"], "width": 43}],
            "fileName": "Сырьевые позиции.xlsx",
        },
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


async def test_realization_missing_period_is_a_bad_request(client: AsyncClient, db_session: AsyncSession) -> None:
    """A year the «Периоды» dictionary does not hold is a 400, never a 500."""
    email = "realization-period@example.ru"
    now = datetime.now(UTC)
    user = User(email=email, name="Realization Period", created_at=now, updated_at=now)
    db_session.add(user)
    await db_session.flush()
    await grant_project_role(db_session, user.id, KPPK_PROJECT_KEY, ProjectRole.ASSET_WORKER)
    await activate_project_user(db_session, user.id, KPPK_PROJECT_KEY)
    await db_session.commit()
    headers = signed_identity_headers(email=email, name="Realization Period")

    org = (await db_session.exec(select(KppkOrg))).first()
    assert org is not None
    response = await client.get(
        "/api/kppk/realization",
        # 2100 is the upper bound the query accepts and can never be seeded.
        params={"orgId": str(org.id), "slice": "bp", "month": 1, "year": 2100},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Период")
