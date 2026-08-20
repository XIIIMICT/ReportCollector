"""Tests for the «Ремонт» project API: meta, plan read, cell edits, lifecycle, undo/redo."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from conftest import signed_identity_headers
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")
TEST_SESSION_ID = "test-session"
RENOVATION_SCOPE = "renovationPlan/"


def _now() -> datetime:
    return datetime.now(UTC)


async def _row_by_asset_code(
    session: AsyncSession,
    code: str,
    *,
    working: bool = True,
) -> RenovationPlanRow:
    """Return the row of the working plan for the asset (rows repeat in archives)."""
    asset = (await session.exec(select(RAsset).where(col(RAsset.code) == code))).one()
    statement = select(RenovationPlanRow).where(col(RenovationPlanRow.asset_id) == asset.id)
    if working:
        statement = statement.join(RenovationPlan, col(RenovationPlanRow.plan_id) == RenovationPlan.id).where(
            col(RenovationPlan.state) == RPlanState.WORKING
        )
    return (await session.exec(statement)).one()


async def _patch(client: AsyncClient, cells: list[dict]) -> tuple[int, dict]:
    response = await client.patch(
        "/api/renovation/plan",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": cells},
    )
    return response.status_code, response.json()


async def _grant_role(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    role: RProjectRole,
    block: RBlock | None,
    asset_codes: list[str],
) -> str:
    """Create a renovation user with role/block/assets; return the user id."""
    now = _now()
    user = User(email=email, name=name, created_at=now, updated_at=now)
    session.add(user)
    await session.flush()
    project = (await session.exec(select(Project).where(col(Project.key) == RENOVATION_PROJECT_KEY))).one()
    session.add(
        RUserRole(
            user_id=user.id,
            project_id=project.id,
            role=role,
            block=block,
            is_active=True,
            created_at=now,
            updated_at=now,
            updated_by="test",
        )
    )
    for code in asset_codes:
        asset = (await session.exec(select(RAsset).where(col(RAsset.code) == code))).one()
        session.add(
            RUserAsset(user_id=user.id, project_id=project.id, asset_id=asset.id, created_at=now, updated_at=now)
        )
    await session.commit()
    return str(user.id)


async def test_meta_lists_form_fields_and_catalogs(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/meta", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["fields"]) == 216
    assert len(body["catalogs"]) == 5
    assert {asset["code"] for asset in body["assets"]} >= {"bsk", "vos"}
    col_003 = next(field for field in body["fields"] if field["dbName"] == "col_003")
    assert col_003["isCalculated"] is True
    assert col_003["isReadOnly"] is True
    col_011 = next(field for field in body["fields"] if field["dbName"] == "col_011")
    assert col_011["isReadOnly"] is False
    assert col_011["cellType"] == "text"
    labels = {catalog["id"]: catalog["label"] for catalog in body["catalogs"]}
    assert labels["work_category"] == "Категория работ"
    assert len(body["headerRows"]) == 11
    assert {row["level"] for row in body["headerRows"]} == set(range(11))
    assert len(body["headerCells"]) == 346
    header_cell = body["headerCells"][0]
    assert {"id", "text", "startPosition", "colSpan", "rowSpan", "level", "excelRange"} <= set(header_cell)


async def test_read_plan_returns_rows_with_labels_and_ids(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "working"
    assert body["baselineGeneration"] == 1
    assert len(body["rows"]) == 2
    by_asset = {row["assetCode"]: row for row in body["rows"]}
    bsk = by_asset["bsk"]
    assert bsk["values"]["col_017"] == "Остановочный капитальный ремонт"
    assert bsk["values"]["col_018"] == "ТР"
    assert bsk["values"]["col_028"] == 10.0
    assert bsk["values"]["col_002"] == "АО «БСК»"
    assert bsk["renovationId"] == "2027_BSKR_1.1.1._1382467_00"
    assert bsk["missingComponents"] == []
    assert by_asset["vos"]["renovationId"] == "2027_VOZR_1.1.1._2247000_00"


async def test_read_plan_versions_lists_working_plan(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plans", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    versions = response.json()
    assert len(versions) == 1
    assert versions[0]["state"] == "working"
    assert versions[0]["rowCount"] == 2


async def test_meta_and_read_require_project_access(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plan", headers=VIEWER_HEADERS)
    assert response.status_code == 403
    assert "Ремонт" in response.json()["detail"]


async def test_patch_edits_recalculates_and_journals(
    client: AsyncClient,
    db_session: AsyncSession,
    store,
) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_028", "value": 15}])
    assert status == 200
    assert body["applied"] == 1
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["col_026"] == 30.0
    assert calculated["col_031"] == 35.0
    assert len(body["renovationIds"]) == 1
    assert body["renovationIds"][0]["value"] == "2027_BSKR_1.1.1._1382467_00"

    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in response.json()["rows"] if r["assetCode"] == "bsk")
    assert row_read["values"]["col_028"] == 15.0

    admin = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    can_undo, _ = await store.undo_state(str(admin.id), TEST_SESSION_ID, RENOVATION_SCOPE)
    assert can_undo == 1


async def test_patch_multiple_cells_in_one_request(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(
        client,
        [
            {"rowId": str(row.id), "field": "col_028", "value": 15},
            {"rowId": str(row.id), "field": "col_029", "value": 25},
        ],
    )
    assert status == 200
    assert body["applied"] == 2
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["col_031"] == 40.0


async def test_patch_accepts_reference_labels(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_017", "value": "ПИР"}])
    assert status == 200
    assert body["applied"] == 1
    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in response.json()["rows"] if r["assetCode"] == "bsk")
    assert row_read["values"]["col_017"] == "ПИР"


async def test_patch_rejects_unknown_reference_label(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_017", "value": "Нет такого"}])
    assert status == 422
    assert "не найдено" in body["detail"]


async def test_patch_rejects_calculated_field(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_026", "value": 5}])
    assert status == 422
    assert "только для чтения" in body["detail"]


async def test_patch_rejects_unknown_field(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_999", "value": "x"}])
    assert status == 422
    assert "Неизвестное поле" in body["detail"]


async def test_patch_rejects_unknown_row(client: AsyncClient) -> None:
    status, _ = await _patch(client, [{"rowId": str(uuid4()), "field": "col_028", "value": 15}])
    assert status == 404


async def test_patch_rejects_bad_date(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    status, _ = await _patch(client, [{"rowId": str(row.id), "field": "col_024", "value": "не дата"}])
    assert status == 422


async def test_patch_date_field_publishes_json_safe_values(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # A date edit drives the col_203 date-shift calculation; the journal and
    # the collab cell_changed payload must carry ISO strings, not raw date
    # objects (json.dumps has no date default — the Redis store raises).
    row = await _row_by_asset_code(db_session, "bsk")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_024", "value": "2027-02-15"}])
    assert status == 200
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["col_203"] == "2027-01-16"


async def test_block_owner_edits_only_own_block(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_id = await _grant_role(
        db_session,
        email="block_owner@example.ru",
        name="Block Owner",
        role=RProjectRole.BLOCK_OWNER,
        block=RBlock.BABAEVA,
        asset_codes=["bsk"],
    )
    headers = signed_identity_headers(email="block_owner@example.ru", name="Block Owner")
    row = await _row_by_asset_code(db_session, "bsk")
    ok = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "col_202", "value": True}]},
    )
    assert ok.status_code == 200
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "col_004", "value": "чужой блок"}]},
    )
    assert denied.status_code == 403
    assert "блока" in denied.json()["detail"]
    # The user record exists — sanity check to keep the helper used.
    assert owner_id


async def test_asset_worker_edits_only_own_asset(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _grant_role(
        db_session,
        email="asset_worker@example.ru",
        name="Asset Worker",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TASHPAEV,
        asset_codes=["vos"],
    )
    headers = signed_identity_headers(email="asset_worker@example.ru", name="Asset Worker")
    vos = await _row_by_asset_code(db_session, "vos")
    bsk = await _row_by_asset_code(db_session, "bsk")
    ok = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(vos.id), "field": "col_004", "value": "своя строка"}]},
    )
    assert ok.status_code == 200
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(bsk.id), "field": "col_004", "value": "чужая строка"}]},
    )
    assert denied.status_code == 403


async def test_undo_restores_previous_value(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    await _patch(client, [{"rowId": str(row.id), "field": "col_028", "value": 15}])
    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationPlan"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    assert body["field"] == "col_028"
    assert body["value"] == 10.0
    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in plan.json()["rows"] if r["assetCode"] == "bsk")
    assert row_read["values"]["col_028"] == 10.0


async def test_redo_reapplies_undone_value(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    row = await _row_by_asset_code(db_session, "bsk")
    await _patch(client, [{"rowId": str(row.id), "field": "col_028", "value": 15}])
    await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationPlan"},
    )
    response = await client.post(
        "/api/renovation/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationPlan"},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert response.json()["value"] == 15.0


async def test_submit_and_approve_plan_lifecycle(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    submit = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    assert submit.status_code == 200
    assert submit.json()["state"] == "pending_approval"

    approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    assert approve.status_code == 200
    archived = approve.json()["archived"]
    working = approve.json()["working"]
    assert archived["state"] == "archived"
    assert working["state"] == "working"
    assert working["baselineGeneration"] == 2
    assert archived["rowCount"] == 2
    assert working["rowCount"] == 2

    again = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    assert again.status_code == 404

    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert plan.json()["baselineGeneration"] == 2
    bsk = next(row for row in plan.json()["rows"] if row["assetCode"] == "bsk")
    assert bsk["values"]["col_028"] == 10.0


async def test_submit_requires_admin_or_block_owner(client: AsyncClient, db_session: AsyncSession) -> None:
    await _grant_role(
        db_session,
        email="approver@example.ru",
        name="Approver",
        role=RProjectRole.APPROVER,
        block=None,
        asset_codes=[],
    )
    headers = signed_identity_headers(email="approver@example.ru", name="Approver")
    response = await client.post("/api/renovation/plan/submit", headers=headers)
    assert response.status_code == 403


async def test_approve_requires_admin_or_approver(client: AsyncClient, db_session: AsyncSession) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    await _grant_role(
        db_session,
        email="block_owner2@example.ru",
        name="Block Owner 2",
        role=RProjectRole.BLOCK_OWNER,
        block=RBlock.BORODIICHUK,
        asset_codes=["bsk"],
    )
    headers = signed_identity_headers(email="block_owner2@example.ru", name="Block Owner 2")
    response = await client.post("/api/renovation/plan/approve", headers=headers)
    assert response.status_code == 403


async def test_approver_reads_but_cannot_edit(client: AsyncClient, db_session: AsyncSession) -> None:
    await _grant_role(
        db_session,
        email="approver2@example.ru",
        name="Approver 2",
        role=RProjectRole.APPROVER,
        block=None,
        asset_codes=[],
    )
    headers = signed_identity_headers(email="approver2@example.ru", name="Approver 2")
    plan = await client.get("/api/renovation/plan", headers=headers)
    assert plan.status_code == 200
    assert len(plan.json()["rows"]) == 2
    row = await _row_by_asset_code(db_session, "bsk")
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "col_028", "value": 1}]},
    )
    assert denied.status_code == 403


async def test_patch_rejected_when_no_working_plan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    row = await _row_by_asset_code(db_session, "bsk", working=False)
    status, _ = await _patch(client, [{"rowId": str(row.id), "field": "col_028", "value": 15}])
    assert status == 404


async def test_read_archived_plan_by_id(client: AsyncClient) -> None:
    submit = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    archived_id = approve.json()["archived"]["id"]
    archived = await client.get(
        "/api/renovation/plan",
        headers=ADMIN_HEADERS,
        params={"plan_id": archived_id},
    )
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"
    assert len(archived.json()["rows"]) == 2
    assert submit.json()["id"] == archived_id


async def test_add_row_appends_new_asset_row(client: AsyncClient, db_session: AsyncSession) -> None:
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "vos"))).one()
    response = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        json={"assetId": str(asset.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["position"] == 2
    assert body["assetCode"] == "vos"
    assert body["values"]["col_002"] == asset.name
    assert isinstance(body["renovationId"], str)
    assert body["renovationId"]

    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    rows = plan.json()["rows"]
    assert len(rows) == 3
    assert [row["position"] for row in rows] == [0, 1, 2]


async def test_add_row_restricted_to_assigned_assets(client: AsyncClient, db_session: AsyncSession) -> None:
    await _grant_role(
        db_session,
        email="asset_worker3@example.ru",
        name="Asset Worker 3",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TASHPAEV,
        asset_codes=["vos"],
    )
    headers = signed_identity_headers(email="asset_worker3@example.ru", name="Asset Worker 3")
    vos = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "vos"))).one()
    bsk = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "bsk"))).one()
    own = await client.post(
        "/api/renovation/plan/rows",
        headers=headers,
        json={"assetId": str(vos.id)},
    )
    assert own.status_code == 200
    denied = await client.post(
        "/api/renovation/plan/rows",
        headers=headers,
        json={"assetId": str(bsk.id)},
    )
    assert denied.status_code == 403


async def test_delete_row_removes_working_row(client: AsyncClient, db_session: AsyncSession) -> None:
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "vos"))).one()
    added = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        json={"assetId": str(asset.id)},
    )
    response = await client.delete(
        f"/api/renovation/plan/rows/{added.json()['id']}",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 204
    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert len(plan.json()["rows"]) == 2


async def test_delete_baseline_row_is_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "vos"))).one()
    added = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        json={"assetId": str(asset.id)},
    )
    assert added.json()["position"] == 2
    baseline_row = await _row_by_asset_code(db_session, "bsk")
    denied = await client.delete(
        f"/api/renovation/plan/rows/{baseline_row.id}",
        headers=ADMIN_HEADERS,
    )
    assert denied.status_code == 409
    assert "базовой линии" in denied.json()["detail"]
    allowed = await client.delete(
        f"/api/renovation/plan/rows/{added.json()['id']}",
        headers=ADMIN_HEADERS,
    )
    assert allowed.status_code == 204


async def test_plan_read_exposes_baseline_boundary(client: AsyncClient) -> None:
    working = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert working.json()["baselineBoundary"] is None
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    refreshed = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert refreshed.json()["baselineBoundary"] == 1


async def test_diff_compares_archived_versions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first_approve = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    first_approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    first_id = first_approve.json()["archived"]["id"]

    row = await _row_by_asset_code(db_session, "bsk")
    await _patch(client, [{"rowId": str(row.id), "field": "col_028", "value": 15}])
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    second_approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    second_id = second_approve.json()["archived"]["id"]
    assert second_id != first_id

    diff = await client.get("/api/renovation/plan/diff", headers=ADMIN_HEADERS, params={"plan_id": second_id})
    assert diff.status_code == 200
    body = diff.json()
    assert body["comparedToPlanId"] == first_id
    bsk_diff = next(item for item in body["rows"] if item["position"] == 0)
    assert bsk_diff["isNew"] is False
    assert {"field": "col_028", "oldValue": 10.0, "newValue": 15.0} in bsk_diff["cells"]
    assert all(item["isNew"] is False for item in body["rows"])
    assert body["deletedRows"] == []


async def test_diff_marks_new_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    first_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "vos"))).one()
    await client.post("/api/renovation/plan/rows", headers=ADMIN_HEADERS, json={"assetId": str(asset.id)})
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    second_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]

    diff = await client.get("/api/renovation/plan/diff", headers=ADMIN_HEADERS, params={"plan_id": second_id})
    body = diff.json()
    assert body["comparedToPlanId"] == first_id
    new_row = next(item for item in body["rows"] if item["position"] == 2)
    assert new_row["isNew"] is True
    assert new_row["assetName"] == asset.name
    assert all(cell["oldValue"] is None for cell in new_row["cells"])
    assert any(cell["field"] == "col_003" for cell in new_row["cells"])
    assert body["deletedRows"] == []


async def test_diff_rejects_non_archived_plan(client: AsyncClient) -> None:
    working = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    response = await client.get(
        "/api/renovation/plan/diff",
        headers=ADMIN_HEADERS,
        params={"plan_id": working.json()["id"]},
    )
    assert response.status_code == 409


async def test_export_working_plan_renders_merged_header(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plan/export", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.content[:2] == b"PK"
    assert "remont_plan" in response.headers["content-disposition"]

    worksheet = load_workbook(BytesIO(response.content)).active
    assert worksheet["A1"].value == "№"
    assert any(str(r) == "A1:A11" for r in worksheet.merged_cells.ranges)
    assert any(str(r) == "D1:AE1" for r in worksheet.merged_cells.ranges)
    assert worksheet["B2"].value == "Предприятие"
    assert worksheet.freeze_panes == "C12"
    assert worksheet["A12"].value == 1
    assert worksheet["B12"].value == "АО «БСК»"
    assert worksheet["C12"].value == "2027_BSKR_1.1.1._1382467_00"
    assert worksheet.max_column == 217
    assert worksheet.max_row == 13


async def test_export_archived_plan_by_id(client: AsyncClient) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    archived_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]
    response = await client.get(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        params={"plan_id": archived_id},
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
