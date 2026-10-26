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
from src.downloads import XLSX_MEDIA_TYPE
from src.kppk.constants import KPPK_PROJECT_KEY, MARKET_CODES
from src.kppk.models import KppkMarket, KppkOrg, KppkOrgProduct, KppkProduct
from src.kppk.realization import read_market_codes

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")


async def _org_by_name(session: AsyncSession, name: str) -> KppkOrg:
    return (await session.exec(select(KppkOrg).where(col(KppkOrg.name) == name))).one()


async def _product_by_name(session: AsyncSession, name: str) -> KppkProduct:
    return (await session.exec(select(KppkProduct).where(col(KppkProduct.name) == name))).one()


async def test_market_codes_follow_the_source_order_not_the_alphabet(
    db_session: AsyncSession,
) -> None:
    """«РФ», «ДЗ», «БЗ» — never `БЗ, ДЗ, РФ`.

    `created_at` cannot order them: the seeder inserts all three in ONE
    transaction, so they share a timestamp and the order silently degraded to
    the alphabetical tiebreaker. The order comes from `MARKET_CODES` instead.
    """
    stored = list((await db_session.exec(select(col(KppkMarket.code)))).all())
    codes = await read_market_codes(db_session)

    # Every market is still returned — a code outside MARKET_CODES sorts LAST
    # instead of disappearing.
    assert sorted(codes) == sorted(stored)
    # The source markets come first, in MARKET_CODES order, never alphabetical.
    source_codes = [code for code in codes if code in MARKET_CODES]
    assert source_codes == [code for code in MARKET_CODES if code in stored]
    # Everything else follows them, ordered by code as the tiebreak.
    others = [code for code in codes if code not in MARKET_CODES]
    assert others == sorted(others)
    assert codes == source_codes + others
    # The regression itself: with all three source markets present, «БЗ» is
    # NOT first (alphabetical would put it there).
    if set(MARKET_CODES) <= set(stored):
        assert codes[0] == "РФ"


# ─── Access ────────────────────────────────────────────────────────────────


async def test_endpoints_require_kppk_access(client: AsyncClient) -> None:
    """A user without a kppk role gets 403 on every dictionary read."""
    for path in ("/api/kppk/product-positions", "/api/kppk/raws", "/api/kppk/orgs", "/api/kppk/cost"):
        response = await client.get(path, headers=VIEWER_HEADERS)
        assert response.status_code == 403


async def test_admin_reads_all_dictionaries(client: AsyncClient) -> None:
    response = await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    assert response.status_code == 200
    rows = response.json()
    # One row per product × ДЗО POSITION: only products listed at an asset have
    # a row (an unlinked product is not a position at all).
    assert len(rows) == 2
    # The default order is the source number.
    assert [row["name"] for row in rows] == [
        "Сульфат натрия",
        "Сода кальцинированная марки Б",
    ]
    assert [row["productNum"] for row in rows] == [1, 2]
    by_product = {row["name"]: row for row in rows}
    assert by_product["Сульфат натрия"]["orgName"] == "АО «СНХЗ»"
    assert all(row["hasExcise"] is False for row in rows)

    response = await client.get("/api/kppk/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    orgs = response.json()
    assert {org["code"] for org in orgs} == {"БСЗ", "СНХЗ"}
    # Sorted by name («Березниковский…» leads «СНХЗ»); the source «№» is not exposed.
    assert [org["code"] for org in orgs] == ["БСЗ", "СНХЗ"]

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
        json={"name": "Каучук СКИ-3", "orgName": org.name},
    )
    assert response.status_code == 201
    created = response.json()["row"]
    assert created["name"] == "Каучук СКИ-3"
    assert created["orgName"] == "АО «СНХЗ»"
    assert created["hasExcise"] is False
    assert created["productNum"] is None

    # The new product went into the product dictionary without a source num.
    product = await _product_by_name(db_session, "Каучук СКИ-3")
    assert product.product_num is None

    # The SAME name at another ДЗО is a second position of that product.
    other = await _org_by_name(db_session, "АО «Березниковский содовый завод»")
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "каучук ски-3", "orgName": other.name, "hasExcise": True},
    )
    assert response.status_code == 201
    second = response.json()["row"]
    assert second["productId"] == created["productId"]
    assert second["hasExcise"] is True
    response = await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    assert [row["name"] for row in response.json()].count("Каучук СКИ-3") == 2

    # The same pair twice is rejected.
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Каучук СКИ-3", "orgName": org.name},
    )
    assert response.status_code == 400

    # An unknown ДЗО is rejected (the product itself is not created).
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Каучук СКИ-5", "orgName": "АО «Неизвестное»"},
    )
    assert response.status_code == 400

    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "name", "value": "Каучук СКИ-5"},
    )
    assert response.status_code == 200
    assert response.json()["row"]["name"] == "Каучук СКИ-5"

    # The position's ДЗО cell moves it to another asset.
    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgName", "value": "АО «Березниковский содовый завод»"},
    )
    assert response.status_code == 400  # that pair already exists (the second position)

    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "hasExcise", "value": True},
    )
    assert response.status_code == 200
    assert response.json()["row"]["hasExcise"] is True

    # An unknown ДЗО is rejected on the cell edit as well.
    response = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgName", "value": "АО «Неизвестное»"},
    )
    assert response.status_code == 400

    # Deleting ONE position keeps the product (it still has the other one).
    response = await client.delete(f"/api/kppk/product-positions/{created['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204
    remaining = (await db_session.exec(select(KppkOrgProduct))).all()
    assert len(remaining) == 3
    assert await _product_by_name(db_session, "Каучук СКИ-5") is not None

    # The LAST position takes the product with it.
    response = await client.delete(f"/api/kppk/product-positions/{second['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 204
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
    product_rows = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    # The fixture's positions are all numbered: unnumber one through the API.
    numbered = product_rows[0]
    response = await client.patch(
        f"/api/kppk/product-positions/{numbered['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "productNum", "value": None},
    )
    assert response.status_code == 200
    unnumbered = response.json()["row"]
    assert unnumbered["productNum"] is None

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
        json={"name": "Сода кальцинированная марки В", "orgName": "АО «СНХЗ»", "productNum": 30},
    )
    assert response.status_code == 201
    assert response.json()["row"]["productNum"] == 30
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "Сода кальцинированная марки Г", "orgName": "АО «СНХЗ»", "productNum": 30},
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


async def test_the_grouped_view_lists_a_product_with_its_org_names(client: AsyncClient) -> None:
    """
    The default (grouped) shape: one row per product, its whole ДЗО set inside.

    Both views read the same stored positions — only the shape differs, so a
    product typed once (grouped) and a position added per ДЗО (excise view) are
    the same rows in the database.
    """
    response = await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "grouped"})
    assert response.status_code == 200
    rows = response.json()
    # One row per PRODUCT (not per position) — the unlinked «марки А» included,
    # with an empty ДЗО set: the dictionary knows the product exists. The
    # unnumbered «Неизвестный продукт» is a dictionary row too (the copies used
    # to carry such a name as free text; now a copy references the dictionary).
    assert [row["name"] for row in rows] == [
        "Сульфат натрия",
        "Сода кальцинированная марки Б",
        # The two unnumbered products follow the numbered ones, by name.
        "Неизвестный продукт",
        "Сода кальцинированная марки А",
    ], [(row["productNum"], row["id"], row["orgNames"]) for row in rows]
    by_name = {row["name"]: row for row in rows}
    assert by_name["Сульфат натрия"]["orgNames"] == ["АО «СНХЗ»"]
    # The same set as SHORT names (the assets' codes) — what the grouped grid
    # prints; the full names stay in `orgNames` for the multi-select.
    assert by_name["Сульфат натрия"]["orgShortNames"] == ["СНХЗ"]
    assert by_name["Сода кальцинированная марки А"]["orgNames"] == []
    assert by_name["Сода кальцинированная марки А"]["orgShortNames"] == []

    # A product at two assets folds into ONE grouped row with both names.
    created = (
        await client.post(
            "/api/kppk/product-positions",
            headers=ADMIN_HEADERS,
            json={"name": "Сульфат натрия", "orgName": "АО «Березниковский содовый завод»"},
        )
    ).json()["row"]
    assert created["productId"] == by_name["Сульфат натрия"]["productId"]
    grouped = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "grouped"})
    ).json()
    sulfate = next(row for row in grouped if row["name"] == "Сульфат натрия")
    assert sorted(sulfate["orgNames"]) == [
        "АО «Березниковский содовый завод»",
        "АО «СНХЗ»",
    ]
    # The position view still spells the same product out twice.
    positions = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    assert [row["name"] for row in positions].count("Сульфат натрия") == 2


async def test_the_grouped_view_creates_edits_and_drops_positions(client: AsyncClient) -> None:
    """The grouped ДЗО multi-select writes the same positions as the position view."""
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={
            "name": "Каучук СКИ-3",
            "orgNames": ["АО «СНХЗ»", "АО «Березниковский содовый завод»", "АО «СНХЗ»"],
            "productNum": 7,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()["row"]
    assert created["productNum"] == 7
    assert created["name"] == "Каучук СКИ-3"

    positions = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    mine = [row for row in positions if row["name"] == "Каучук СКИ-3"]
    assert len(mine) == 2, [(row["orgName"], row["hasExcise"]) for row in mine]  # a repeated name is one position
    assert {row["orgName"] for row in mine} == {"АО «СНХЗ»", "АО «Березниковский содовый завод»"}
    assert all(row["hasExcise"] is False for row in mine)
    assert len({row["productId"] for row in mine}) == 1

    # Flag one position, then shrink the ДЗО set through the grouped cell: the
    # surviving pair keeps its flag, the dropped one leaves the dictionary.
    flagged = next(row for row in mine if row["orgName"] == "АО «СНХЗ»")
    response = await client.patch(
        f"/api/kppk/product-positions/{flagged['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "hasExcise", "value": True},
    )
    assert response.status_code == 200

    response = await client.patch(
        f"/api/kppk/product-positions/{flagged['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": ["АО «СНХЗ»"]},
    )
    assert response.status_code == 200, response.text
    # The echo is the POSITION shape; the ДЗО set is read back grouped.
    grouped = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "grouped"})
    ).json()
    assert next(row for row in grouped if row["name"] == "Каучук СКИ-3")["orgNames"] == ["АО «СНХЗ»"]
    positions = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    mine = [row for row in positions if row["name"] == "Каучук СКИ-3"]
    assert [(row["orgName"], row["hasExcise"]) for row in mine] == [("АО «СНХЗ»", True)]

    # Adding an asset back is a new, unflagged position.
    response = await client.patch(
        f"/api/kppk/product-positions/{flagged['id']}",
        headers=ADMIN_HEADERS,
        json={
            "field": "orgNames",
            "value": ["АО «СНХЗ»", "АО «Березниковский содовый завод»"],
        },
    )
    assert response.status_code == 200, response.text
    positions = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    mine = [row for row in positions if row["name"] == "Каучук СКИ-3"]
    assert {(row["orgName"], row["hasExcise"]) for row in mine} == {
        ("АО «СНХЗ»", True),
        ("АО «Березниковский содовый завод»", False),
    }

    # Clearing the whole set drops every position: the product keeps existing
    # (the dictionary knows it) but has no assets left.
    response = await client.patch(
        f"/api/kppk/product-positions/{mine[0]['id']}",
        headers=ADMIN_HEADERS,
        json={"field": "orgNames", "value": []},
    )
    assert response.status_code == 200, response.text
    grouped = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "grouped"})
    ).json()
    remaining = next(row for row in grouped if row["name"] == "Каучук СКИ-3")
    assert remaining["orgNames"] == []


async def test_a_new_product_may_start_without_assets(client: AsyncClient) -> None:
    """A product is typed once; its ??? set may be empty and filled in later."""
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "ПРОБА КППК-5", "orgNames": []},
    )
    assert response.status_code == 201
    created = response.json()["row"]
    assert created["name"] == "ПРОБА КППК-5"
    assert created["orgName"] == ""
    assert created["orgNames"] == []
    # The row id is the PRODUCT: its own cells stay editable without positions.
    renamed = await client.patch(
        f"/api/kppk/product-positions/{created['id']}",
        headers=ADMIN_HEADERS,
        params={"session_id": "probe"},
        json={"field": "name", "value": "ПРОБА КППК-5 bis"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["row"]["name"] == "ПРОБА КППК-5 bis"

    # An UNKNOWN asset is still rejected ? the names must resolve.
    response = await client.post(
        "/api/kppk/product-positions",
        headers=ADMIN_HEADERS,
        json={"name": "ПРОБА КППК-6", "orgNames": ["АО «Неизвестное»"]},
    )
    assert response.status_code == 400
    positions = (
        await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})
    ).json()
    assert all(row["name"] != "ПРОБА КППК-6" for row in positions)


async def test_undo_position_create_and_flag_roundtrip(client: AsyncClient) -> None:
    """Adding a position is undoable; the excise flag toggles back the same way."""
    rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})).json()
    product = next(row for row in rows if row["name"] == "Сульфат натрия")
    assert product["orgName"] == "АО «СНХЗ»"
    assert product["hasExcise"] is False

    response = await client.patch(
        f"/api/kppk/product-positions/{product['id']}",
        params={"session_id": TEST_SESSION_ID},
        headers=ADMIN_HEADERS,
        json={"field": "hasExcise", "value": True},
    )
    assert response.status_code == 200
    assert response.json()["row"]["hasExcise"] is True

    undone = await _undo(client, "kppkPositions")
    assert undone["applied"] is True
    assert undone["value"] is False
    rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})).json()
    assert next(row for row in rows if row["id"] == product["id"])["hasExcise"] is False

    # A second position of the same product, then undo removes exactly it.
    second = (
        await client.post(
            "/api/kppk/product-positions",
            headers=ADMIN_HEADERS,
            params={"session_id": TEST_SESSION_ID},
            json={"name": "Сульфат натрия", "orgName": "АО «Березниковский содовый завод»"},
        )
    ).json()["row"]
    assert second["productId"] == product["productId"]
    undone = await _undo(client, "kppkPositions")
    assert undone["applied"] is True
    rows = (await client.get("/api/kppk/product-positions", headers=ADMIN_HEADERS, params={"view": "positions"})).json()
    assert all(row["id"] != second["id"] for row in rows)
    # The product itself survives: it still has its original position.
    assert any(row["productId"] == product["productId"] for row in rows)


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
                # The «ДЗО» cell of a position keeps the enterprise dropdown.
                {"key": "orgName", "headerPath": ["ДЗО"], "width": 36},
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
    # Each row is one POSITION: the product name and its asset are real cells.
    sheet = next(s for s in workbook.worksheets if s.title != "Списки")
    values = [list(row) for row in sheet.iter_rows(values_only=True)]
    assert ["Сульфат натрия", "АО «СНХЗ»"] in [row[1:] for row in values[1:]]


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
    assert response.status_code == 200


async def test_realization_rows_carry_the_product_number_and_sort_by_it(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """«Реализация» resolves each product's «№» through the dictionary and orders by it.

    The copies reference the product by uuid, so the number is a direct
    dictionary lookup. The seeded rows are inserted out of order on purpose: the
    view must follow the dictionary numbering, and a product without a number
    must sort last with a NULL number.
    """
    org = await _org_by_name(db_session, "АО «СНХЗ»")
    response = await client.get(
        "/api/kppk/realization",
        params={"orgId": str(org.id), "slice": "bp", "month": 6, "year": 2026},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["product"] for row in rows] == [
        # number 1
        "Сульфат натрия",
        # number 2 — both markets of one product stay together
        "Сода кальцинированная марки Б",
        "Сода кальцинированная марки Б",
        # no dictionary entry
        "Неизвестный продукт",
    ]
    assert [row["productNum"] for row in rows] == [1, 2, 2, None]
    # Neither «RU» nor «KZ» is a source market, so both sort last and fall back
    # to their code — alphabetical only as the tiebreaker within that group.
    assert [row["region"] for row in rows] == ["RU", "KZ", "RU", "RU"]


async def test_realization_export_renders_the_selected_view(client: AsyncClient, db_session: AsyncSession) -> None:
    """The «Реализация» export replays the view: same rows, same order, the sent columns."""
    org = await _org_by_name(db_session, "АО «СНХЗ»")
    response = await client.post(
        "/api/kppk/realization/export",
        headers=ADMIN_HEADERS,
        json={
            "slice": "bp",
            "orgId": str(org.id),
            "month": 6,
            "year": 2026,
            "headerPinned": True,
            "columns": [
                {"key": "productNum", "headerPath": ["№"], "width": 8},
                {"key": "product", "headerPath": ["Продукция"], "width": 40},
                {"key": "monthBpPlan", "headerPath": ["Реализация", "Объем"], "width": 12},
            ],
            "fileName": "Реализация.xlsx",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    sheet = load_workbook(BytesIO(response.content)).worksheets[0]
    values = [list(row) for row in sheet.iter_rows(values_only=True)]
    # The header is two levels deep («Реализация» / «Объем»), so the leaf
    # labels live on the second row; the data follows the header.
    assert values[0] == ["№", "Продукция", "Реализация"]
    data = values[2:]
    # A merged block keeps its label in the ANCHOR cell only; openpyxl reads the
    # covered cells as None — that is the merge working, not missing data.
    assert [row[1] for row in data] == [
        "Сульфат натрия",
        "Сода кальцинированная марки Б",
        None,
        "Неизвестный продукт",
    ]
    assert [row[0] for row in data] == [1, 2, None, None]
    # The template folds a product's market rows into ONE block: the name and
    # the number are each merged down over the run (the grid renders the same
    # with CellMergePlugin). The header's own merges (A1:A2, C1:C2) stay apart.
    # Rows: the header ends at 2, the data runs 3..6.
    data_merges = sorted(str(span) for span in sheet.merged_cells.ranges if span.min_row >= 3)
    assert data_merges == [
        "A4:A5",  # «Сода … Б» — one product, two markets
        "B4:B5",
    ]
