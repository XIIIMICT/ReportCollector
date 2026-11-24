"""Tests for the «Ремонт» project API: meta, plan read, cell edits, lifecycle, undo/redo."""

import json
import re
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import IO, Any, cast
from urllib.parse import unquote
from uuid import uuid4
from zipfile import ZipFile

import pytest
from conftest import signed_identity_headers
from httpx2 import AsyncClient
from openpyxl import load_workbook
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.export import RenovationWorkbookWriter, build_plan_sheet_layout
from src.renovation.export_jobs import ExportJobSpec
from src.renovation.export_jobs import export_jobs as export_jobs_manager
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.service import read_plan_export_meta, read_plan_facets, recompute_plan_rows

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
VIEWER_HEADERS = signed_identity_headers(email="viewer@example.ru", name="Viewer User")
TEST_SESSION_ID = "test-session"
RENOVATION_SCOPE = "renovationPlan/"


def _now() -> datetime:
    return datetime.now(UTC)


def _disposition_filename(disposition: str) -> str:
    """Decode the RFC 5987 UTF-8 file name a browser would save."""
    match = re.search(r"filename\*\s*=UTF-8''([^;]+)", disposition)
    if match:
        return unquote(match.group(1))
    plain = re.search(r'filename="?([^";]+)"?', disposition)
    return unquote(plain.group(1)) if plain else ""


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
            updater_user_id=None,
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
    # 217 form fields: the retired «Корректировка» is gone and «Номер сметы»
    # (estimate_number) is a real field — the first column of the КСиР block.
    assert len(body["fields"]) == 217
    assert len(body["catalogs"]) == 5
    assert {asset["code"] for asset in body["assets"]} >= {"BSKR", "VOZR"}
    work_or_repair_code = next(field for field in body["fields"] if field["dbName"] == "work_or_repair_code")
    # The renovation-ID formula is OFF: the code is a plain typed value, loaded
    # from the template as-is (see RENOVATION_ID_FORMULA_ENABLED).
    assert work_or_repair_code["isCalculated"] is False
    assert work_or_repair_code["isReadOnly"] is False
    process_position = next(field for field in body["fields"] if field["dbName"] == "process_position")
    assert process_position["isReadOnly"] is False
    assert process_position["cellType"] == "text"
    labels = {catalog["id"]: catalog["label"] for catalog in body["catalogs"]}
    assert labels["work_category"] == "Категория работ"
    assert len(body["headerRows"]) == 11
    assert {row["level"] for row in body["headerRows"]} == set(range(11))
    assert len(body["headerCells"]) == 347
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
    bsk = by_asset["BSKR"]
    assert bsk["values"]["work_category_id"] == "Остановочный капитальный ремонт"
    assert bsk["values"]["work_type_id"] == "ТР"
    assert bsk["values"]["work_cost_rub_ex_vat"] == 10.0
    assert bsk["values"]["col_002"] == "АО «БСК»"
    assert bsk["renovationId"] == "2027_BSKR_1.1.1_1382467"
    assert bsk["missingComponents"] == []
    assert by_asset["VOZR"]["renovationId"] == "2027_VOZR_1.1.1_2247000"


async def test_generic_facets_group_and_page_in_sql(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A facet window never materializes every distinct value in Python."""
    from src.renovation.models import RAsset

    plan = (await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    assets = list((await db_session.exec(select(RAsset))).all())
    statements: list[str] = []
    real_exec = db_session.exec

    def spy(statement: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        statements.append(str(statement))
        return real_exec(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "exec", spy)
    result = await read_plan_facets(
        db_session, assets, plan_id=plan.id, column="work_description", offset=100, limit=25
    )
    sql = "\n".join(statements)
    assert "GROUP BY" in sql
    assert "LIMIT" in sql
    assert "OFFSET" in sql
    assert len(result.values) <= 25


async def test_read_plan_facets_sorted_distinct(client: AsyncClient) -> None:
    """Facets return distinct display values sorted alphabetically, scoped like rows."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "process_position"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["column"] == "process_position"
    # The seed has two rows: «Насос НК» and «Шкаф ПЛК», both filled.
    assert body["total"] == 2
    assert [item["value"] for item in body["values"]] == ["Насос НК", "Шкаф ПЛК"]
    assert [item["count"] for item in body["values"]] == [1, 1]
    assert body["hasNulls"] is False


async def test_read_plan_facets_shared_value(client: AsyncClient) -> None:
    """A column shared by both seeded rows counts twice under one distinct value."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "lotting_activity_direction_code"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["values"] == [{"value": "1.1.1.", "count": 2}]
    assert body["hasNulls"] is False


async def test_read_plan_facets_initial_window_works_for_every_column_type(
    client: AsyncClient,
) -> None:
    """The FIRST facet request has no search — number/date columns must still load."""
    number = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "work_cost_rub_ex_vat"},
    )
    assert number.status_code == 200, number.text
    assert [item["value"] for item in number.json()["values"]] == ["5", "10"]

    date = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "work_end_date"},
    )
    assert date.status_code == 200, date.text
    assert date.json()["values"] == [{"value": "2027-01-31", "count": 2}]


async def test_read_plan_facets_search_is_applied_before_paging(client: AsyncClient) -> None:
    """A search returns matches outside the first unfiltered facet window."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "process_position", "search_kind": "text", "search_query": "насос"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["values"] == [{"value": "Насос НК", "count": 1}]
    assert body["rowCount"] == 1


async def test_read_plan_facets_numeric_search_filters_before_offset(client: AsyncClient) -> None:
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "work_cost_rub_ex_vat", "search_kind": "range", "search_min": 6, "offset": 0, "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["values"] == [{"value": "10", "count": 1}]


async def test_read_plan_facets_search_follows_the_field_type(client: AsyncClient) -> None:
    """
    The facet search shape follows the FIELD, and a wrong shape is refused.

    «Дефектная ведомость/ВОР №» became TEXT in migration 0030, so its facet
    takes a substring query and rejects the numeric MIN–MAX pair a stale client
    still sends; the opposite holds for a numeric column.
    """
    text = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "defect_list_or_bill_of_quantities_number", "search_kind": "text", "search_query": "138"},
    )
    assert text.status_code == 200, text.text
    assert text.json()["values"] == [{"value": "1382467", "count": 1}]

    numeric_window = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "defect_list_or_bill_of_quantities_number", "search_kind": "range", "search_min": 100},
    )
    assert numeric_window.status_code == 422, numeric_window.text

    text_on_number = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "work_cost_rub_ex_vat", "search_kind": "text", "search_query": "1"},
    )
    assert text_on_number.status_code == 422, text_on_number.text


async def test_a_value_filter_refuses_a_range_window(client: AsyncClient) -> None:
    """A MIN–MAX window on a value-filtered column is a stale client, not an empty match."""
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={
            "filters": [{"column": "defect_list_or_bill_of_quantities_number", "from": "1", "to": "9"}],
            "sorting": [],
        },
    )
    assert response.status_code == 422, response.text


async def test_read_plan_facets_date_search_uses_client_timezone(client: AsyncClient) -> None:
    minimum = datetime(2027, 1, 15, tzinfo=UTC).timestamp() * 1000
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={
            "column": "work_end_date",
            "search_kind": "datetime",
            "search_min": minimum,
            "search_tz": "UTC",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["values"] == [{"value": "2027-01-31", "count": 2}]


async def test_read_plan_facets_reports_empty_count_on_every_page(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await _row_by_asset_code(db_session, "BSKR")
    second = await _row_by_asset_code(db_session, "VOZR")
    first.process_position = None
    second.process_position = ""
    await db_session.commit()
    for offset in (0, 200):
        response = await client.get(
            "/api/renovation/plan/facets",
            headers=ADMIN_HEADERS,
            params={"column": "process_position", "offset": offset},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["hasNulls"] is True
        assert body["nullCount"] == 2
        assert body["values"] == []


async def test_read_plan_facets_asset_column(client: AsyncClient) -> None:
    """The synthetic asset column facets by the joined asset name."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "__assetName"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["value"] for item in body["values"]] == ["АО «БСК»", "АО «Волжский Оргсинтез»"]


async def test_renovation_id_filter_lists_buckets_and_codes(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    The ID column keeps «Корректные»/«Некорректные» first and lists codes too.

    With the generator off a code is judged by uniqueness: «Некорректные» are the
    codes repeated inside the plan plus the empty ones — exactly what blocks
    submit.
    """
    facets = await client.get(
        "/api/renovation/plan/facets", headers=ADMIN_HEADERS, params={"column": "work_or_repair_code"}
    )
    assert facets.status_code == 200
    body = facets.json()
    assert body["hasNulls"] is False
    assert [item["value"] for item in body["presets"]] == ["Корректные", "Некорректные"]
    assert [item["value"] for item in body["values"]] == sorted(["2027_BSKR_1.1.1_1382467", "2027_VOZR_1.1.1_2247000"])
    assert body["total"] == body["totalWithoutSearch"] == body["rowCount"] == 2
    assert [item["count"] for item in body["presets"]] == [2, 0]

    searched = await client.post(
        "/api/renovation/plan/facets/query",
        headers=ADMIN_HEADERS,
        params={"column": "work_or_repair_code"},
        json={"filters": [], "search_kind": "text", "search_query": "vozr"},
    )
    assert searched.status_code == 200, searched.text
    assert [item["value"] for item in searched.json()["values"]] == ["2027_VOZR_1.1.1_2247000"]
    assert searched.json()["presets"] == body["presets"]
    assert searched.json()["total"] == searched.json()["rowCount"] == 1
    assert searched.json()["totalWithoutSearch"] == 2
    page = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "work_or_repair_code", "offset": 1, "limit": 1},
    )
    assert page.json()["values"] == body["values"][1:]
    assert page.json()["presets"] == body["presets"]

    async def rows_for(bucket: str) -> int:
        filters = json.dumps([{"column": "work_or_repair_code", "values": [bucket], "includeNulls": False}])
        response = await client.post(
            "/api/renovation/plan/rows/query",
            headers=ADMIN_HEADERS,
            params={"offset": 0, "limit": 100},
            json={"filters": json.loads(filters)},
        )
        assert response.status_code == 200, response.text
        return response.json()["total"]

    assert await rows_for("Корректные") == 2
    assert await rows_for("Некорректные") == 0
    assert await rows_for("2027_BSKR_1.1.1_1382467") == 1

    # A repeated code makes BOTH of its rows incorrect.
    first = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    second = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 2))).one()
    second.work_or_repair_code = first.work_or_repair_code
    await db_session.flush()
    facets = await client.get(
        "/api/renovation/plan/facets", headers=ADMIN_HEADERS, params={"column": "work_or_repair_code"}
    )
    assert [item["count"] for item in facets.json()["presets"]] == [0, 2]
    assert facets.json()["values"] == [{"value": first.work_or_repair_code, "count": 2}]
    assert await rows_for("Некорректные") == 2

    # An empty code is incorrect on its own, even with no duplicate around.
    second.work_or_repair_code = "2027_BSKR_1.1.1_9999999"
    first.work_or_repair_code = None
    await db_session.flush()
    facets = await client.get(
        "/api/renovation/plan/facets", headers=ADMIN_HEADERS, params={"column": "work_or_repair_code"}
    )
    assert [item["count"] for item in facets.json()["presets"]] == [1, 1]
    assert facets.json()["hasNulls"] is True
    assert facets.json()["nullCount"] == 1
    assert await rows_for("Некорректные") == 1


async def test_code_facets_search_literal_symbols_unicode_and_preceding_scope(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first = await _row_by_asset_code(db_session, "BSKR")
    second = await _row_by_asset_code(db_session, "VOZR")
    first.work_or_repair_code = "ß_СОДА%"
    second.work_or_repair_code = "СС_СОДА1"
    await db_session.flush()
    for query, expected in [
        ("ss_сода%", [first.work_or_repair_code]),
        ("%", [first.work_or_repair_code]),
        ("не найдено", []),
    ]:
        result = await client.post(
            "/api/renovation/plan/facets/query",
            headers=ADMIN_HEADERS,
            params={"column": "work_or_repair_code"},
            json={"search_kind": "text", "search_query": query},
        )
        assert result.status_code == 200, result.text
        assert [entry["value"] for entry in result.json()["values"]] == expected
        assert len(result.json()["presets"]) == 2
    scoped = await client.post(
        "/api/renovation/plan/facets/query",
        headers=ADMIN_HEADERS,
        params={"column": "work_or_repair_code"},
        json={
            "filters": [
                {"column": "__assetName", "values": ["АО «БСК»"]},
                {"column": "work_or_repair_code", "values": ["not found"]},
            ]
        },
    )
    assert scoped.json()["values"] == [{"value": first.work_or_repair_code, "count": 1}]
    assert [entry["count"] for entry in scoped.json()["presets"]] == [1, 0]


async def test_literal_code_matching_group_label_survives_filter_and_export(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    row.work_or_repair_code = "Корректные"
    await db_session.flush()
    query = {"filters": [{"column": "work_or_repair_code", "values": ["Корректные"], "valueMode": "exact"}]}
    rows = await client.post("/api/renovation/plan/rows/query", headers=ADMIN_HEADERS, json=query)
    assert rows.status_code == 200, rows.text
    assert rows.json()["total"] == 1
    assert rows.json()["rows"][0]["id"] == str(row.id)
    exported = await client.post("/api/renovation/plan/export", headers=ADMIN_HEADERS, json=query)
    assert exported.status_code == 200, exported.text
    worksheet = load_workbook(BytesIO(exported.content)).active
    assert worksheet.max_row == 14
    assert worksheet["B14"].value == "Корректные"


async def test_edit_keeps_the_renovation_id_when_a_component_changes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    work_or_repair_code is a typed value now: editing its old inputs leaves the code alone.

    The generator is off (`RENOVATION_ID_FORMULA_ENABLED`), so clearing the ДВ
    number (defect_list_or_bill_of_quantities_number) must not rewrite the code behind the user's back.
    """
    row = await _row_by_asset_code(db_session, "BSKR")
    before = row.work_or_repair_code
    assert before
    status, _ = await _patch(
        client, [{"rowId": str(row.id), "field": "defect_list_or_bill_of_quantities_number", "value": None}]
    )
    assert status == 200
    await db_session.refresh(row)
    assert row.work_or_repair_code == before


async def test_edit_renovation_id_directly_is_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The code itself is an ordinary editable cell (it used to be read-only)."""
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(
        client, [{"rowId": str(row.id), "field": "work_or_repair_code", "value": "  СВОЙ-КОД 1  "}]
    )
    assert status == 200, body
    await db_session.refresh(row)
    # Text cells are stored trimmed, the value itself is kept as typed.
    assert row.work_or_repair_code == "СВОЙ-КОД 1"


async def test_edit_rejects_a_value_breaking_the_field_rule(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """work_cost_rub_ex_vat is «не может быть отрицательным» — a negative edit is refused."""
    row = await _row_by_asset_code(db_session, "BSKR")
    status, _ = await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": -1}])
    assert status == 422


async def test_read_plan_reports_calculated_rule_violations(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A calculated field breaking its rule is flagged for the grid's red tint.

    «Продолжительность, дни» (work_duration_days = work_end_date − work_start_date) cannot be negative;
    the dates are edited directly, so the violation is only visible after the
    recalculation and must be reported as an invalid field.
    """
    row = await _row_by_asset_code(db_session, "BSKR")
    # Start after end → work_duration_days becomes negative. The edit is applied and the
    # row recalced by the same PATCH; the dates themselves have no minimum
    # rule, so only the derived duration breaks one.
    status, body = await _patch(
        client,
        [
            {"rowId": str(row.id), "field": "work_start_date", "value": "2027-03-01"},
            {"rowId": str(row.id), "field": "work_end_date", "value": "2027-01-01"},
        ],
    )
    assert status == 200

    # The PATCH reports the recalculated VALUE only — the grid derives the red
    # tint from it against the field's rules (a live recalculation carries no
    # metadata, and a `cell:changed` broadcast reaches other users the same
    # way). Pinning the shape here keeps that contract honest.
    recalculated = {(cell["rowId"], cell["field"]): cell["value"] for cell in body["calculated"]}
    assert recalculated[(str(row.id), "work_duration_days")] < 0

    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    bsk = next(item for item in response.json()["rows"] if item["assetCode"] == "BSKR")
    assert "work_duration_days" in bsk["invalidFields"]


async def test_read_plan_facets_with_offsets(client: AsyncClient) -> None:
    """The distinct-value window is lazy: offset/limit page over the sorted list."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "process_position", "offset": 1, "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["value"] for item in body["values"]] == ["Шкаф ПЛК"]


async def test_read_plan_facets_unknown_column(client: AsyncClient) -> None:
    """Garbage column rejected with 422."""
    response = await client.get(
        "/api/renovation/plan/facets",
        headers=ADMIN_HEADERS,
        params={"column": "no_such_col"},
    )
    assert response.status_code == 422


async def test_facets_follow_preceding_filters_and_keep_first_step_choices(client: AsyncClient) -> None:
    filters = [
        {"column": "process_position", "values": ["Шкаф ПЛК"]},
        {"column": "__assetName", "values": ["АО «БСК»"]},
        {"column": "work_category_id", "values": ["not selected"]},
    ]
    second = await client.post(
        "/api/renovation/plan/facets/query",
        headers=ADMIN_HEADERS,
        params={"column": "__assetName"},
        json={"filters": filters},
    )
    assert second.status_code == 200, second.text
    assert second.json()["values"] == [{"value": "АО «БСК»", "count": 1}]
    first = await client.post(
        "/api/renovation/plan/facets/query",
        headers=ADMIN_HEADERS,
        params={"column": "process_position"},
        json={"filters": filters},
    )
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 2
    assert first.json()["rowCount"] == 2
    empty = await client.post(
        "/api/renovation/plan/facets/query",
        headers=ADMIN_HEADERS,
        params={"column": "bpof_section"},
        json={"filters": filters},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == empty.json()["rowCount"] == 0


async def test_large_filter_body_and_validation(client: AsyncClient) -> None:
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        json={"filters": [{"column": "process_position", "values": ["Шкаф ПЛК"] + [str(i) for i in range(5000)]}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    invalid = await client.post("/api/renovation/plan/rows/query", headers=ADMIN_HEADERS, json={"filters": "bad"})
    assert invalid.status_code == 422


async def test_plan_rows_position_range(client: AsyncClient) -> None:
    """The row-number («№») range narrows the window and the total."""
    everything = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
    )
    assert everything.json()["total"] == 2

    only_first = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100, "position_from": 1, "position_to": 1},
    )
    assert only_first.status_code == 200
    body = only_first.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    assert body["rows"][0]["position"] == 1

    from_two = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100, "position_from": 2},
    )
    assert from_two.json()["total"] == 1
    assert from_two.json()["rows"][0]["position"] == 2


async def test_plan_rows_support_server_sorting_by_display_and_numeric_values(client: AsyncClient) -> None:
    """The remote plan orders the full scope, not only the loaded window."""
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"sorting": [{"column": "process_position", "order": "desc"}]},
    )
    assert response.status_code == 200
    assert [row["values"]["process_position"] for row in response.json()["rows"]] == ["Шкаф ПЛК", "Насос НК"]

    numeric = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"sorting": [{"column": "work_cost_rub_ex_vat", "order": "asc"}]},
    )
    assert numeric.status_code == 200
    assert [row["values"]["work_cost_rub_ex_vat"] for row in numeric.json()["rows"]] == [5.0, 10.0]


async def test_plan_export_job_rejects_unknown_sort_column(client: AsyncClient) -> None:
    """Invalid sort metadata is a client error before a worker is queued."""
    response = await client.post(
        "/api/renovation/plan/export/jobs",
        headers=ADMIN_HEADERS,
        json={"sorting": [{"column": "not-a-plan-column", "order": "asc"}]},
    )
    assert response.status_code == 422


async def test_plan_rows_total_all_is_the_whole_plan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The «всего» counter (totalAll) is the plan's row count, filters or not."""
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    row.work_or_repair_code = None  # empty — «Некорректные», so «Корректные» hides it
    await db_session.flush()

    filters = json.dumps([{"column": "work_or_repair_code", "values": ["Корректные"], "includeNulls": False}])
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["totalAll"] == 2


async def test_plan_summary_sums_numeric_fields(client: AsyncClient) -> None:
    """The pinned summary sums the numeric fields of the filtered plan."""
    response = await client.get("/api/renovation/plan/summary", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    values = response.json()["values"]
    # The fixture's two rows carry work_cost_rub_ex_vat = 10 and 5.
    assert values["work_cost_rub_ex_vat"] == 15.0
    # Non-additive columns are omitted entirely: the defect-statement number,
    # the duration, the physical volumes and the MTR-readiness block.
    for field in (
        "defect_list_or_bill_of_quantities_number",
        "work_duration_days",
        "adjustment_work_volume_unit",
        "adjustment_work_volume_quantity",
        "em_contracting_cs_pct",
        "equipment_materials_delivery_direct_supply_readiness_percent",
        "contractor_equipment_materials_supply_readiness_percent",
    ):
        assert field not in values

    filtered = await client.get(
        "/api/renovation/plan/summary",
        headers=ADMIN_HEADERS,
        params={"position_from": 1, "position_to": 1},
    )
    assert filtered.status_code == 200
    assert filtered.json()["values"]["work_cost_rub_ex_vat"] == 10.0


async def test_plan_rows_filter_by_text_value(client: AsyncClient) -> None:
    """A text column filter keeps only matching rows (server-side, whole plan)."""
    filters = json.dumps([{"column": "process_position", "values": ["Шкаф ПЛК"], "includeNulls": False}])
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["assetCode"] for row in body["rows"]] == ["BSKR"]


async def test_plan_rows_filter_by_reference_label(client: AsyncClient) -> None:
    """A reference filter maps the display label back to its value id."""
    # «Остановочный капитальный ремонт» is shared by both seeded rows.
    filters = json.dumps([{"column": "work_category_id", "values": ["Остановочный капитальный ремонт"]}])
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2


async def test_plan_rows_filters_combine_with_and(client: AsyncClient) -> None:
    """Two column filters combine with AND: only the row matching both remains."""
    filters = json.dumps(
        [
            {"column": "work_category_id", "values": ["Остановочный капитальный ремонт"]},
            {"column": "process_position", "values": ["Насос НК"]},
        ]
    )
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["assetCode"] for row in body["rows"]] == ["VOZR"]


async def test_plan_rows_filter_empties_keeps_only_nulls(client: AsyncClient) -> None:
    """A values-empty `includeNulls` filter keeps only the empty bucket."""
    # bpof_section is «Ремонтный бюджет» on both seeded rows, so ask for the empty
    # bucket only — nothing matches.
    filters = json.dumps([{"column": "bpof_section", "values": [], "includeNulls": True}])
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_plan_rows_filter_asset_column(client: AsyncClient) -> None:
    """The synthetic asset column filter matches by asset name."""
    filters = json.dumps([{"column": "__assetName", "values": ["АО «БСК»"], "includeNulls": False}])
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": json.loads(filters)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["assetCode"] for row in body["rows"]] == ["BSKR"]


async def test_plan_rows_filter_audit_instant_by_window(client: AsyncClient) -> None:
    """An «updatedAt» filter is a MIN–MAX window over the rows, both ends inclusive.

    An instant column has no value list (every row carries its own timestamp), so
    the client sends `from`/`to` and the server keeps the rows inside them.
    """
    unfiltered = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 1},
    )
    assert unfiltered.status_code == 200
    total = unfiltered.json()["total"]
    assert total > 0
    stamp = unfiltered.json()["rows"][0]["updatedAt"]

    # A window around the row's own instant keeps it…
    inside = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": [{"column": "updatedAt", "from": stamp, "to": stamp}]},
    )
    assert inside.status_code == 200
    assert inside.json()["total"] >= 1
    # …and a window entirely BEFORE it keeps none of them.
    none = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={
            "filters": [
                {
                    "column": "updatedAt",
                    "from": "2000-01-01T00:00:00+00:00",
                    "to": "2000-01-02T00:00:00+00:00",
                }
            ]
        },
    )
    assert none.status_code == 200
    assert none.json()["total"] == 0


async def test_plan_rows_filter_audit_date_excludes_other_minutes(client: AsyncClient) -> None:
    """A minute nobody wrote in matches no row — the range is not a no-op."""
    response = await client.post(
        "/api/renovation/plan/rows/query",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
        json={"filters": [{"column": "updatedAt", "values": ["1999-01-01T00:00"]}]},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_plan_rows_rejects_bad_filters(client: AsyncClient) -> None:
    """A malformed filters param is a 422."""
    response = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100, "filters": "not-json"},
    )
    assert response.status_code == 422


async def test_plan_rows_filter_include_nulls_on_typed_columns(client: AsyncClient) -> None:
    """
    `includeNulls` must never compare a typed column to ''.

    The facet collapses NULL and the empty string into one «Пустые» bucket, but
    only a textual column can hold an empty string. Comparing a uuid/date/
    boolean/numeric column to '' makes Postgres fail the cast and 500s the whole
    window, which is what the default (nulls-included) header filter triggers.
    """
    for column in (
        "defect_list_or_bill_of_quantities_number",
        "work_start_date",
        "has_materials_in_work_or_repair",
        "work_category_id",
    ):
        filters = json.dumps([{"column": column, "values": [], "includeNulls": True}])
        response = await client.post(
            "/api/renovation/plan/rows/query",
            headers=ADMIN_HEADERS,
            params={"offset": 0, "limit": 100},
            json={"filters": json.loads(filters)},
        )
        assert response.status_code == 200, (column, response.text)


async def test_plan_rows_filter_nulls_with_kept_number_and_date_values(client: AsyncClient) -> None:
    """Keeping a value and the empty bucket on a typed column still answers 200."""
    cases = [
        ({"column": "defect_list_or_bill_of_quantities_number", "values": ["1382467"], "includeNulls": True}, 1),
        ({"column": "work_start_date", "values": ["2027-01-01"], "includeNulls": True}, 2),
        ({"column": "work_category_id", "values": ["Остановочный капитальный ремонт"], "includeNulls": True}, 2),
        # has_materials_in_work_or_repair is empty on both seeded rows: only the empty bucket can match.
        ({"column": "has_materials_in_work_or_repair", "values": ["Да"], "includeNulls": True}, 2),
        ({"column": "has_materials_in_work_or_repair", "values": ["Да"], "includeNulls": False}, 0),
    ]
    for filter_, expected_total in cases:
        response = await client.post(
            "/api/renovation/plan/rows/query",
            headers=ADMIN_HEADERS,
            params={"offset": 0, "limit": 100},
            json={"filters": [filter_]},
        )
        assert response.status_code == 200, (filter_, response.text)
        assert response.json()["total"] == expected_total, filter_


async def test_read_plan_rows_omits_empty_cells(client: AsyncClient) -> None:
    """NULL cells are omitted from `values` (sparse payload); set ones survive."""
    response = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
    )
    assert response.status_code == 200, response.text
    row = next(entry for entry in response.json()["rows"] if entry["assetCode"] == "BSKR")
    values = row["values"]
    # Set cells keep their transformed value.
    assert values["process_position"] == "Шкаф ПЛК"
    assert values["defect_list_or_bill_of_quantities_number"] == "1382467"
    assert values["work_start_date"] == "2027-01-01"
    assert values["work_category_id"] == "Остановочный капитальный ремонт"
    # The synthetic «Предприятие» column is never null — it is "" when unset.
    assert values["col_002"] == "АО «БСК»"
    # Empty cells are absent rather than null.
    assert "inventory_number" not in values
    assert "bpof_section" not in values or values["bpof_section"] is not None


async def test_read_plan_rows_tolerates_invalid_renovation_id(client: AsyncClient, db_session: AsyncSession) -> None:
    """A row whose old ID components are nonsense reads back as-is (no 500)."""
    row = await _row_by_asset_code(db_session, "BSKR")
    row.work_or_repair_code = None
    row.defect_list_or_bill_of_quantities_number = "12345678"  # more than the required seven digits
    db_session.add(row)
    await db_session.commit()

    response = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    bad = next(entry for entry in body["rows"] if entry["assetCode"] == "BSKR")
    # The generator is off: nothing is predicted, an empty cell reads empty.
    assert bad["renovationId"] is None
    assert bad["missingComponents"] == []


async def test_export_tolerates_invalid_renovation_id(client: AsyncClient, db_session: AsyncSession) -> None:
    """The export recomputes missing IDs too, so it must survive the same bad row."""
    row = await _row_by_asset_code(db_session, "BSKR")
    row.work_or_repair_code = None
    row.defect_list_or_bill_of_quantities_number = "12345678"
    db_session.add(row)
    await db_session.commit()

    response = await client.get("/api/renovation/plan/export", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text


async def test_read_empty_assetless_draft_row(client: AsyncClient, db_session: AsyncSession) -> None:
    """An asset-less draft row is readable by an admin as an empty row."""
    plan = (await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    row = RenovationPlanRow(
        plan_id=plan.id,
        asset_id=None,
        position=100,
        baseline_generation=plan.baseline_generation,
        revision=0,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=None,
    )
    db_session.add(row)
    await db_session.commit()

    full = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert full.status_code == 200
    draft = next(r for r in full.json()["rows"] if r["position"] == 100)
    assert draft["assetId"] is None
    assert draft["assetName"] is None
    assert draft["assetCode"] is None
    assert draft["values"]["col_002"] == ""
    assert draft["renovationId"] is None


async def test_read_plan_rows_windows_with_total(client: AsyncClient) -> None:
    response = await client.get(
        "/api/renovation/plan/rows",
        params={"offset": 0, "limit": 1},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["rows"]) == 1

    response = await client.get(
        "/api/renovation/plan/rows",
        params={"offset": 1, "limit": 1},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    # The second window returns the other asset (rows are ordered by position).
    assert body["rows"][0]["position"] != 0


async def test_read_plan_versions_lists_working_plan(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plans", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    versions = response.json()
    assert len(versions) == 1
    assert versions[0]["state"] == "working"
    assert versions[0]["rowCount"] == 2


async def test_plan_windows_with_sparse_positions_keep_offsets_and_anchor(client, db_session):
    rows = (await db_session.exec(select(RenovationPlanRow).order_by(col(RenovationPlanRow.position)))).all()
    for row, position in zip(rows, (10, 30), strict=True):
        row.position = position
        db_session.add(row)
    await db_session.commit()
    for offset, positions in ((0, [10]), (1, [30]), (2, []), (10, [])):
        response = await client.get(
            "/api/renovation/plan/rows",
            headers=ADMIN_HEADERS,
            params={"offset": offset, "limit": 1, "anchor_position": 10},
        )
        assert response.status_code == 200
        body = response.json()
        assert [row["position"] for row in body["rows"]] == positions
        assert body["total"] == body["totalAll"] == 2
        assert body["anchorOffset"] == 0


async def test_meta_and_read_require_project_access(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plan", headers=VIEWER_HEADERS)
    assert response.status_code == 403
    assert "Ремонт" in response.json()["detail"]


async def test_patch_edits_recalculates_and_journals(
    client: AsyncClient,
    db_session: AsyncSession,
    store,
) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    assert status == 200
    assert body["applied"] == 1
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["work_duration_days"] == 30.0
    assert calculated["total_cost_rub_ex_vat"] == 35.0
    assert len(body["renovationIds"]) == 1
    assert body["renovationIds"][0]["value"] == "2027_BSKR_1.1.1_1382467"

    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in response.json()["rows"] if r["assetCode"] == "BSKR")
    assert row_read["values"]["work_cost_rub_ex_vat"] == 15.0

    admin = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    can_undo, _ = await store.undo_state(str(admin.id), TEST_SESSION_ID, RENOVATION_SCOPE)
    assert can_undo == 1


async def test_patch_recomputes_only_the_dependency_closure(client: AsyncClient, db_session: AsyncSession) -> None:
    """AUD-28: an edit recomputes the affected formulas, not every derived column."""
    row = await _row_by_asset_code(db_session, "BSKR")
    # work_duration_days (duration = work_end_date - work_start_date) does not depend on work_cost_rub_ex_vat (work cost).
    row.work_duration_days = Decimal(999)
    await db_session.flush()

    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    assert status == 200
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["total_cost_rub_ex_vat"] == 35.0
    # The unrelated derived value is reported as stored, not recomputed.
    assert calculated["work_duration_days"] == 999.0


async def test_patch_multiple_cells_in_one_request(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(
        client,
        [
            {"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15},
            {"rowId": str(row.id), "field": "customer_materials_cost_rub_ex_vat", "value": 25},
        ],
    )
    assert status == 200
    assert body["applied"] == 2
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["total_cost_rub_ex_vat"] == 40.0


async def test_patch_accepts_reference_labels(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_category_id", "value": "ПИР"}])
    assert status == 200
    assert body["applied"] == 1
    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in response.json()["rows"] if r["assetCode"] == "BSKR")
    assert row_read["values"]["work_category_id"] == "ПИР"


async def test_patch_rejects_unknown_reference_label(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_category_id", "value": "Нет такого"}])
    assert status == 422
    assert "не найдено" in body["detail"]


async def test_patch_prefetches_reference_ids_for_many_cells(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch of reference cells uses one catalog snapshot, not one SELECT per cell."""
    from src.renovation import cell_mutations

    row = await _row_by_asset_code(db_session, "BSKR")
    single_lookups: list[object] = []
    original = cell_mutations._resolve_reference_id

    async def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        single_lookups.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(cell_mutations, "_resolve_reference_id", spy)
    cells = [
        {"rowId": str(row.id), "field": "equipment_criticality_group_id", "value": "3"},
        {"rowId": str(row.id), "field": "work_category_id", "value": "ТОиР"},
        {"rowId": str(row.id), "field": "work_type_id", "value": "КР"},
        {"rowId": str(row.id), "field": "cost_allocation_type_id", "value": "OPEX"},
        {"rowId": str(row.id), "field": "execution_method_id", "value": "Собственные силы"},
    ]
    status, body = await _patch(client, cells)
    assert status == 200, body
    # Some seed values may already match, but every reference cell still went
    # through the prefetched map — no single-row catalog lookup happened.
    assert body["applied"] >= 3
    assert single_lookups == []

    # The prefetched map is strict exactly like the single-row lookup.
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_category_id", "value": "Нет такого"}])
    assert status == 422
    assert "не найдено" in body["detail"]


async def test_patch_rejects_calculated_field(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_duration_days", "value": 5}])
    assert status == 422
    assert "только для чтения" in body["detail"]


async def test_patch_rejects_unknown_field(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "col_999", "value": "x"}])
    assert status == 422
    assert "Неизвестное поле" in body["detail"]


async def test_patch_rejects_unknown_row(client: AsyncClient) -> None:
    status, _ = await _patch(client, [{"rowId": str(uuid4()), "field": "work_cost_rub_ex_vat", "value": 15}])
    assert status == 404


async def test_patch_rejects_bad_date(client: AsyncClient, db_session: AsyncSession) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    status, _ = await _patch(client, [{"rowId": str(row.id), "field": "work_start_date", "value": "не дата"}])
    assert status == 422


async def test_patch_date_field_publishes_json_safe_values(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # A date edit drives the required_materials_delivery_date date-shift calculation; the journal and
    # the collab cell_changed payload must carry ISO strings, not raw date
    # objects (json.dumps has no date default — the Redis store raises).
    row = await _row_by_asset_code(db_session, "BSKR")
    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_start_date", "value": "2027-02-15"}])
    assert status == 200
    calculated = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert calculated["required_materials_delivery_date"] == "2027-01-16"


async def test_block_owner_edits_only_own_block(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_id = await _grant_role(
        db_session,
        email="block_owner@example.ru",
        name="Block Owner",
        role=RProjectRole.BLOCK_OWNER,
        block=RBlock.MTO,
        asset_codes=["BSKR"],
    )
    headers = signed_identity_headers(email="block_owner@example.ru", name="Block Owner")
    row = await _row_by_asset_code(db_session, "BSKR")
    ok = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "has_materials_in_work_or_repair", "value": True}]},
    )
    assert ok.status_code == 200
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "production_name", "value": "чужой блок"}]},
    )
    assert denied.status_code == 403
    assert "блока" in denied.json()["detail"]
    # The user record exists — sanity check to keep the helper used.
    assert owner_id


async def test_block_owner_sees_all_assets_without_grants(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A block owner needs no per-asset grant: it implicitly covers all assets.

    Regression: block owners (Техническая дирекция/Дирекция КСиР/Дирекция МТО) were grouped with
    asset_workers in access resolution, so with no r_user_assets rows they saw
    an empty plan and the admin was wrongly forced to grant them assets.
    """
    await _grant_role(
        db_session,
        email="block_owner_noassets@example.ru",
        name="Block Owner (no assets)",
        role=RProjectRole.BLOCK_OWNER,
        block=RBlock.TECH,
        asset_codes=[],
    )
    headers = signed_identity_headers(
        email="block_owner_noassets@example.ru",
        name="Block Owner (no assets)",
    )
    plan = await client.get("/api/renovation/plan/rows", headers=headers)
    assert plan.status_code == 200
    body = plan.json()
    # The block owner sees every seeded asset's rows even without an asset grant.
    assert body["total"] > 0
    codes = {row["assetCode"] for row in body["rows"]}
    assert {"BSKR", "VOZR"} <= codes


async def test_asset_worker_edits_only_own_asset(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _grant_role(
        db_session,
        email="asset_worker@example.ru",
        name="Asset Worker",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TECH,
        asset_codes=["VOZR"],
    )
    headers = signed_identity_headers(email="asset_worker@example.ru", name="Asset Worker")
    vos = await _row_by_asset_code(db_session, "VOZR")
    bsk = await _row_by_asset_code(db_session, "BSKR")
    ok = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(vos.id), "field": "production_name", "value": "своя строка"}]},
    )
    assert ok.status_code == 200
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(bsk.id), "field": "production_name", "value": "чужая строка"}]},
    )
    assert denied.status_code == 403


async def test_undo_restores_previous_value(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    response = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationPlan"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    assert body["field"] == "work_cost_rub_ex_vat"
    assert body["value"] == 10.0
    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    row_read = next(r for r in plan.json()["rows"] if r["assetCode"] == "BSKR")
    assert row_read["values"]["work_cost_rub_ex_vat"] == 10.0


async def test_undo_restores_source_and_recalculates_formula(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.7: Undo restores the source cell AND the formula that depends on it."""
    from src.renovation import router_undo

    published: list[int] = []
    real_publish = router_undo._publish_rows_changed

    async def spy(session: AsyncSession, access: Any) -> None:  # noqa: ANN401
        published.append(1)
        await real_publish(session, access)

    monkeypatch.setattr(router_undo, "_publish_rows_changed", spy)

    row = await _row_by_asset_code(db_session, "BSKR")
    before = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    original = next(r for r in before.json()["rows"] if r["assetCode"] == "BSKR")["values"]
    source_before = original["work_cost_rub_ex_vat"]
    derived_before = original["total_cost_rub_ex_vat"]

    status, body = await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    assert status == 200
    changed = {cell["field"]: cell["value"] for cell in body["calculated"]}
    assert changed["total_cost_rub_ex_vat"] != derived_before

    undone = await client.post(
        "/api/renovation/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": TEST_SESSION_ID},
        json={"section": "renovationPlan"},
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["applied"] is True
    assert undone.json()["value"] == source_before

    after = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    restored = next(r for r in after.json()["rows"] if r["assetCode"] == "BSKR")["values"]
    assert restored["work_cost_rub_ex_vat"] == source_before
    assert restored["total_cost_rub_ex_vat"] == derived_before
    # Other clients learn the recalculated value via rows:changed -> refetch.
    assert published == [1]


async def test_redo_reapplies_undone_value(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    row = await _row_by_asset_code(db_session, "BSKR")
    await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
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
    bsk = next(row for row in plan.json()["rows"] if row["assetCode"] == "BSKR")
    assert bsk["values"]["work_cost_rub_ex_vat"] == 10.0


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
        block=RBlock.KSIR,
        asset_codes=["BSKR"],
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
    row = await _row_by_asset_code(db_session, "BSKR")
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 1}]},
    )
    assert denied.status_code == 403


async def test_admin_can_refine_pending_plan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    row = await _row_by_asset_code(db_session, "BSKR", working=False)
    status, _ = await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    assert status == 200


async def test_only_admin_edits_pending_plan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A version under review is editable only by an admin."""
    await _grant_role(
        db_session,
        email="asset_worker_pend@example.ru",
        name="Asset Worker Pend",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TECH,
        asset_codes=["BSKR"],
    )
    worker_headers = signed_identity_headers(
        email="asset_worker_pend@example.ru",
        name="Asset Worker Pend",
    )
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    row = await _row_by_asset_code(db_session, "BSKR", working=False)

    denied = await client.patch(
        "/api/renovation/plan",
        headers=worker_headers,
        params={"session_id": TEST_SESSION_ID},
        json={"updates": [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 1}]},
    )
    assert denied.status_code == 403


async def test_submit_drops_empty_draft_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    """Rows with no content (or only an asset) are removed when submitting."""
    await client.post("/api/renovation/plan/rows", headers=ADMIN_HEADERS)
    submitted = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    assert submitted.status_code == 200
    assert submitted.json()["rowCount"] == 2


async def test_submit_rejects_incorrect_work_ids(client: AsyncClient, db_session: AsyncSession) -> None:
    """Submit is refused while a row's work ID is empty."""
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    row.work_or_repair_code = None
    await db_session.flush()

    response = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    assert response.status_code == 409
    assert "некорректных" in response.json()["detail"]
    # The version stays editable — the transition did not happen.
    assert (await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)).status_code == 200


async def test_submit_accepts_a_code_carrying_the_null_placeholder(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    `<NULL>` is no longer a verdict on a code.

    It was the generator's mark for a missing component; with the generator off
    the text is whatever the user typed (or the template carried), so a code
    containing it is judged by its uniqueness like any other.
    """
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    row.work_or_repair_code = "2027_BSKR_<NULL>_1382467_00"
    await db_session.flush()

    response = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text


async def test_submit_rejects_duplicate_work_ids(client: AsyncClient, db_session: AsyncSession) -> None:
    """Submit is refused while two rows share the same work ID."""
    first = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    second = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 2))).one()
    second.work_or_repair_code = first.work_or_repair_code
    await db_session.flush()

    response = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    assert response.status_code == 409
    assert "дублирующихся" in response.json()["detail"]


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


async def test_add_row_appends_assetless_draft(client: AsyncClient, db_session: AsyncSession) -> None:
    # An admin adds an instant empty row without choosing an asset.
    response = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["position"] == 3
    assert body["assetId"] is None
    assert body["assetName"] is None
    assert body["assetCode"] is None
    assert body["renovationId"] is None

    plan = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    rows = plan.json()["rows"]
    assert len(rows) == 3
    assert [row["position"] for row in rows] == [1, 2, 3]


async def test_add_row_uses_the_requested_asset(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /plan/rows with an assetId pre-fills the row's asset."""
    vos = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "VOZR"))).one()
    response = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        json={"assetId": str(vos.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assetId"] == str(vos.id)
    assert body["assetCode"] == "VOZR"
    assert body["assetName"] == vos.name

    # The new row is found by the asset filter (it belongs to the asset).
    plan = await client.get(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
        params={"offset": 0, "limit": 100, "asset": vos.name},
    )
    assert plan.json()["total"] == 2


async def test_add_row_rejects_a_foreign_asset(client: AsyncClient, db_session: AsyncSession) -> None:
    """An asset worker cannot add a row for an asset it has no access to."""
    await _grant_role(
        db_session,
        email="asset_worker4@example.ru",
        name="Asset Worker 4",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TECH,
        asset_codes=["VOZR"],
    )
    headers = signed_identity_headers(email="asset_worker4@example.ru", name="Asset Worker 4")
    bsk = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSKR"))).one()
    response = await client.post(
        "/api/renovation/plan/rows",
        headers=headers,
        json={"assetId": str(bsk.id)},
    )
    assert response.status_code == 403


async def test_add_row_for_single_asset_worker_autofills_asset(client: AsyncClient, db_session: AsyncSession) -> None:
    # A single-asset asset worker adds under its only asset (no popup needed).
    await _grant_role(
        db_session,
        email="asset_worker3@example.ru",
        name="Asset Worker 3",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TECH,
        asset_codes=["VOZR"],
    )
    headers = signed_identity_headers(email="asset_worker3@example.ru", name="Asset Worker 3")
    vos = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "VOZR"))).one()
    own = await client.post(
        "/api/renovation/plan/rows",
        headers=headers,
    )
    assert own.status_code == 200
    assert own.json()["assetCode"] == "VOZR"
    assert own.json()["assetId"] == str(vos.id)


async def test_add_row_is_limited_to_admin_and_tech_block_workers(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Only an admin or a tech-block asset worker may add rows.

    Block owners, approvers and other directorates' workers are denied.
    """
    await _grant_role(
        db_session,
        email="block_owner_noadd@example.ru",
        name="Block Owner (no add)",
        role=RProjectRole.BLOCK_OWNER,
        block=RBlock.TECH,
        asset_codes=[],
    )
    await _grant_role(
        db_session,
        email="approver_noadd@example.ru",
        name="Approver (no add)",
        role=RProjectRole.APPROVER,
        block=None,
        asset_codes=[],
    )
    await _grant_role(
        db_session,
        email="ksir_worker_noadd@example.ru",
        name="KSIR worker (no add)",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.KSIR,
        asset_codes=["VOZR"],
    )
    await _grant_role(
        db_session,
        email="tech_worker_canadd@example.ru",
        name="Tech worker (add)",
        role=RProjectRole.ASSET_WORKER,
        block=RBlock.TECH,
        asset_codes=["VOZR"],
    )
    for email, name in (
        ("block_owner_noadd@example.ru", "Block Owner (no add)"),
        ("approver_noadd@example.ru", "Approver (no add)"),
        ("ksir_worker_noadd@example.ru", "KSIR worker (no add)"),
    ):
        denied = await client.post(
            "/api/renovation/plan/rows",
            headers=signed_identity_headers(email=email, name=name),
        )
        assert denied.status_code == 403, email
    allowed = await client.post(
        "/api/renovation/plan/rows",
        headers=signed_identity_headers(email="tech_worker_canadd@example.ru", name="Tech worker (add)"),
    )
    assert allowed.status_code == 200


async def test_assign_asset_to_draft_row(client: AsyncClient, db_session: AsyncSession) -> None:
    """An asset-less draft can be pointed at an asset by an admin."""
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "VOZR"))).one()
    draft = (await client.post("/api/renovation/plan/rows", headers=ADMIN_HEADERS)).json()
    assert draft["assetId"] is None

    assigned = await client.patch(
        f"/api/renovation/plan/rows/{draft['id']}",
        headers=ADMIN_HEADERS,
        json={"assetId": str(asset.id)},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assetCode"] == "VOZR"
    assert assigned.json()["assetName"] == asset.name


async def test_delete_row_removes_working_row(client: AsyncClient, db_session: AsyncSession) -> None:
    added = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
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
    added = await client.post(
        "/api/renovation/plan/rows",
        headers=ADMIN_HEADERS,
    )
    assert added.json()["position"] == 3
    baseline_row = await _row_by_asset_code(db_session, "BSKR")
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
    assert refreshed.json()["baselineBoundary"] == 2


async def test_diff_compares_archived_versions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first_approve = await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    first_approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    first_id = first_approve.json()["archived"]["id"]

    row = await _row_by_asset_code(db_session, "BSKR")
    await _patch(client, [{"rowId": str(row.id), "field": "work_cost_rub_ex_vat", "value": 15}])
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    second_approve = await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)
    second_id = second_approve.json()["archived"]["id"]
    assert second_id != first_id

    diff = await client.get("/api/renovation/plan/diff", headers=ADMIN_HEADERS, params={"plan_id": second_id})
    assert diff.status_code == 200
    body = diff.json()
    assert body["comparedToPlanId"] == first_id
    bsk_diff = next(item for item in body["rows"] if item["position"] == 1)
    assert bsk_diff["isNew"] is False
    assert {"field": "work_cost_rub_ex_vat", "oldValue": 10.0, "newValue": 15.0} in bsk_diff["cells"]
    assert all(item["isNew"] is False for item in body["rows"])
    assert body["deletedRows"] == []


async def test_diff_deleted_row_carries_values(client: AsyncClient, db_session: AsyncSession) -> None:
    """A deleted row in the diff carries its previous values (no extra plan read)."""
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    first_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]

    # Drop a row from the new working plan so the next version lacks it.
    working = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).one()
    victim = (
        await db_session.exec(
            select(RenovationPlanRow)
            .where(col(RenovationPlanRow.plan_id) == working.id)
            .order_by(col(RenovationPlanRow.position))
        )
    ).first()
    assert victim is not None
    victim_position = victim.position
    victim_amount = victim.work_cost_rub_ex_vat
    assert victim_amount is not None
    await db_session.delete(victim)
    await db_session.commit()

    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    second_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]

    diff = await client.get("/api/renovation/plan/diff", headers=ADMIN_HEADERS, params={"plan_id": second_id})
    assert diff.status_code == 200
    body = diff.json()
    assert body["comparedToPlanId"] == first_id
    deleted = next(item for item in body["deletedRows"] if item["position"] == victim_position)
    assert deleted["assetName"]
    assert deleted["values"]["work_cost_rub_ex_vat"] == float(victim_amount)


async def test_diff_marks_new_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    first_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]
    # New rows are added as empty drafts; give it an asset + content so it is a
    # real new row (an empty draft would be dropped again on the next submit).
    asset = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "VOZR"))).one()
    added = (await client.post("/api/renovation/plan/rows", headers=ADMIN_HEADERS)).json()
    new_row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.id) == added["id"]))).one()
    new_row.asset_id = asset.id
    new_row.work_cost_rub_ex_vat = Decimal(15)
    # The plan can only be submitted when every work ID is filled, and the code
    # is a typed value now — set it like an import or a user would.
    new_row.work_or_repair_code = "2027_BSKR_1.1.1_9999999"
    new_row.defect_list_or_bill_of_quantities_number = "9999999"
    new_row.work_start_date = date(2027, 1, 1)
    new_row.lotting_activity_direction_code = "1.1.1."
    db_session.add(new_row)
    await db_session.flush()
    await recompute_plan_rows(db_session, plan_id=new_row.plan_id)
    await db_session.commit()
    await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)
    second_id = (await client.post("/api/renovation/plan/approve", headers=ADMIN_HEADERS)).json()["archived"]["id"]

    diff = await client.get("/api/renovation/plan/diff", headers=ADMIN_HEADERS, params={"plan_id": second_id})
    body = diff.json()
    assert body["comparedToPlanId"] == first_id
    new_row = next(item for item in body["rows"] if item["position"] == 3)
    assert new_row["isNew"] is True
    assert new_row["assetName"] == asset.name
    assert any(cell["oldValue"] is None for cell in new_row["cells"])
    assert any(cell["field"] == "work_or_repair_code" for cell in new_row["cells"])
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
    disposition = response.headers["content-disposition"]
    filename = _disposition_filename(disposition)
    assert filename.startswith("Пообъектный план — рабочая версия (")
    assert filename.endswith(".xlsx")

    worksheet = load_workbook(BytesIO(response.content)).active
    # No row-number column: the sheet starts with «Предприятие» (A), the first
    # form column (work_or_repair_code) is B, the data rows still start dense at 14.
    assert worksheet["A2"].value == "Предприятие"
    assert worksheet.freeze_panes == "B14"
    # Two aggregating rows after the 11 header levels: the number row (12) and
    # the totals row (13); a column without a total merges its number over
    # both — there are no «№»/«Итог» corner labels any more.
    assert str(worksheet["A12"].value).startswith("1")
    assert any(str(r) == "A12:A13" for r in worksheet.merged_cells.ranges)
    assert worksheet["A14"].value == "АО «БСК»"
    assert worksheet["B14"].value == "2027_BSKR_1.1.1_1382467"
    # 217 fields (the retired «Корректировка» is gone, «Номер сметы» added,
    # no row-number column); positions are continuous, so there is no gap.
    assert worksheet.max_column == 217
    assert worksheet.max_row == 15


@pytest.mark.real_schema
async def test_export_job_file_streams_whole_workbook(
    client: AsyncClient,
    db_session: AsyncSession,
    my_engine,
    monkeypatch,
    tmp_path,
) -> None:
    """The background-job file endpoint streams the workbook to the browser.

    Regression: the endpoint used to block until generation finished, so the
    browser showed nothing for minutes. It now streams the file, so the download
    starts at once. The generation worker uses the app session factory (not the
    test one), so this test drives the streaming contract directly: it builds the
    workbook with the real writer and registers a finished job, then reads it back
    through the API.
    """
    user = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    plan = (await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    meta = await read_plan_export_meta(db_session, [], plan_id=plan.id, state=RPlanState.WORKING)

    target = Path(tempfile.gettempdir()) / f"{uuid4()}.xlsx"
    with target.open("wb") as sink:
        writer = RenovationWorkbookWriter(
            cast("IO[bytes]", sink),
            build_plan_sheet_layout(None, meta.totals),
            meta.reference_labels,
            totals=meta.totals,
            row_total=1,
        )
        writer.write_rows([(1, "Test asset", "CODE", {"workshop_name": "Value"})])
        writer.close()

    spec = ExportJobSpec(
        plan_id=meta.plan_id,
        state=meta.state,
        asset_ids=[],
        row_ids=None,
        block=None,
        file_name="Пообъектный план — тест.xlsx",
        total=meta.total,
        cache_key=f"test-{uuid4()}",
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.common.export_jobs import final_path
    from src.common.job_repository import JobRepository

    repository = JobRepository(async_sessionmaker(my_engine, class_=AsyncSession, expire_on_commit=False))
    monkeypatch.setattr(export_jobs_manager, "repository", repository)
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    job = await export_jobs_manager.create(spec, str(user.id))
    claimed = await repository.claim(export_jobs_manager.kind)
    assert claimed is not None
    assert claimed.artifact is not None
    published = final_path(claimed.artifact)
    target.replace(published)
    await repository.finish(claimed, size=published.stat().st_size, total=1)
    try:
        response = await client.get(f"/api/renovation/plan/export/jobs/{job.id}/file", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        # The whole workbook arrives through the streaming response, ZIP magic first.
        assert response.content[:2] == b"PK"
        assert ZipFile(BytesIO(response.content)).testzip() is None
    finally:
        target.unlink(missing_ok=True)
        published.unlink(missing_ok=True)


async def test_export_job_file_unknown_id_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/renovation/plan/export/jobs/missing/file", headers=ADMIN_HEADERS)
    assert response.status_code == 404


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
    assert _disposition_filename(response.headers["content-disposition"]).startswith(
        "Пообъектный план — утверждённая версия ("
    )


async def test_export_pending_plan_filename_suffix(client: AsyncClient) -> None:
    """A pending version exports under the «на согласовании» suffix."""
    pending_id = (await client.post("/api/renovation/plan/submit", headers=ADMIN_HEADERS)).json()["id"]
    response = await client.get(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        params={"plan_id": pending_id},
    )
    assert response.status_code == 200
    assert _disposition_filename(response.headers["content-disposition"]).startswith(
        "Пообъектный план — версия на согласовании ("
    )


async def test_export_working_filename_stamps_last_edit(client: AsyncClient, db_session: AsyncSession) -> None:
    """The working-plan export stamps the latest table edit (max row.updated_at).

    Aging the plan version stamp backwards must not age the export name — it
    keeps reading the most recent row edit (< 5s), not the plan.updated_at.
    """
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    plan.updated_at = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
    db_session.add(plan)
    await db_session.commit()

    response = await client.get("/api/renovation/plan/export", headers=ADMIN_HEADERS)
    filename = _disposition_filename(response.headers["content-disposition"])
    assert filename.startswith("Пообъектный план — рабочая версия (")
    match = re.search(r"\((\d{2}\.\d{2}\.\d{4}, \d{2}\.\d{2}\.\d{2})\)\.xlsx", filename)
    assert match is not None
    stamp = match.group(1)
    # The seed rows were edited just now, so the stamp year is current — not 2020.
    assert f"{datetime.now(UTC):%Y}," in stamp
    assert "2020," not in stamp


async def test_export_visible_rows_only(client: AsyncClient) -> None:
    """POST export with rowIds renders only the requested rows («видимая»)."""
    rows = (await client.get("/api/renovation/plan/rows?offset=0&limit=3", headers=ADMIN_HEADERS)).json()
    visible_ids = [row["id"] for row in rows["rows"][:1]]

    response = await client.post(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        json={"planId": None, "rowIds": visible_ids},
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"

    worksheet = load_workbook(BytesIO(response.content)).active
    # Header rows + the number row (12) + the totals row (13) + exactly the one
    # requested plan row (14).
    assert worksheet.max_row == 14


@pytest.mark.parametrize("header_filter", [False, True])
async def test_export_recalculates_derived_columns(
    client: AsyncClient,
    db_session: AsyncSession,
    header_filter: bool,
) -> None:
    """A derived column («Продолжительность, дни» = work_duration_days) is recomputed for export.

    work_duration_days has no stored value until an edit recalcs the row; the export must
    derive it from work_end_date - work_start_date (else the column is blank on the sheet).
    """
    row = await _row_by_asset_code(db_session, "BSKR")
    row.work_start_date = date(2027, 1, 10)
    row.work_end_date = date(2027, 1, 25)
    row.work_duration_days = None
    work_code = row.work_or_repair_code
    assert work_code
    db_session.add(row)
    await db_session.commit()

    if header_filter:
        response = await client.post(
            "/api/renovation/plan/export",
            headers=ADMIN_HEADERS,
            json={"filters": [{"column": "__assetName", "values": ["АО «БСК»"], "includeNulls": False}]},
        )
    else:
        response = await client.get("/api/renovation/plan/export", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    worksheet = load_workbook(BytesIO(response.content)).active
    assert worksheet.max_row == (14 if header_filter else 15)
    # Recomputing a missing derived value must preserve the editable work code.
    assert worksheet["B14"].value == work_code
    # Hiding estimate_number shifts every later field one column left (and the dropped
    # row-number column one more): work_start_date/work_end_date land on W/X; work_duration_days on Y.
    assert worksheet["W14"].value.date() == date(2027, 1, 10)
    assert worksheet["X14"].value.date() == date(2027, 1, 25)
    # The calculated column keeps its Excel formula, with the recomputed value cached.
    assert worksheet["Y14"].value == "=X14 - W14"
    cached = load_workbook(BytesIO(response.content), data_only=True).active
    assert cached["Y14"].value == 15


async def test_export_block_filters_columns(client: AsyncClient) -> None:
    """A block filter keeps only that block's columns (plus the always-visible)."""
    response = await client.post(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        json={"planId": None, "block": "mto"},
    )
    assert response.status_code == 200
    worksheet = load_workbook(BytesIO(response.content)).active
    # mto fields + col_002 + work_or_repair_code + note (no row-number column).
    assert worksheet.max_column == 15 + 3
    assert worksheet.max_column < 216


async def test_export_visible_filters_asset_and_block(client: AsyncClient) -> None:
    """The server-side «видимая» filter applies asset and block, not rowIds."""
    response = await client.post(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        json={"planId": None, "asset": "АО «БСК»", "block": "tech"},
    )
    assert response.status_code == 200
    worksheet = load_workbook(BytesIO(response.content)).active
    assert worksheet["A14"].value == "АО «БСК»"
    # Only the asset's rows: the seed has rows for other assets, all dropped.
    assets = {worksheet.cell(row=row, column=1).value for row in range(14, worksheet.max_row + 1)}
    assert assets == {"АО «БСК»"}


async def test_export_header_borders_on_every_merged_cell(client: AsyncClient) -> None:
    """Every cell of a merged header range carries the bordered style, not just the anchor.

    Otherwise Excel draws the frame only around the top-left cell and the grid
    looks underdrawn down and right. openpyxl does not expose a border for
    non-anchor merged cells, so this checks the sheet XML style id directly.
    """
    response = await client.get("/api/renovation/plan/export", headers=ADMIN_HEADERS)
    sheet = ZipFile(BytesIO(response.content)).read("xl/worksheets/sheet1.xml").decode()
    # D1:AE1 is merged; D1 holds the text, every other cell must carry `s="1"`.
    # Hiding estimate_number shifts the last header cells one column left (HH/HG).
    styled = {ref: f'<c r="{ref}" s="1"' for ref in ("D1", "E1", "AE1", "AA5", "HH11")}
    for ref, marker in styled.items():
        assert re.search(rf'<c r="{ref}"[^>]* s="1"', sheet), ref


async def test_recompute_plan_rows_materializes_derived_values(db_session: AsyncSession) -> None:
    """The backfill computes the formula fields; the typed code is left alone."""
    plan = (await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    # The seed filled the row; clear the derived values to exercise the backfill.
    row.work_duration_days = None
    row.work_or_repair_code = None
    await db_session.flush()

    result = await recompute_plan_rows(db_session, plan_id=plan.id)
    await db_session.flush()

    assert result.scanned == 2
    assert result.changed == 1
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.position) == 1))).one()
    # work_duration_days = work_end_date - work_start_date (2027-01-31 - 2027-01-01); total_cost_rub_ex_vat = 10 + 20.
    assert row.work_duration_days == Decimal(30)
    assert row.total_cost_rub_ex_vat == Decimal(30)
    # The renovation ID is not derived any more: an empty cell stays empty.
    assert row.work_or_repair_code is None

    # Idempotent: a second pass finds nothing to change.
    second = await recompute_plan_rows(db_session, plan_id=plan.id)
    assert second.scanned == 2
    assert second.changed == 0


async def test_recompute_plan_rows_can_narrow_to_the_rows_an_import_wrote(db_session: AsyncSession) -> None:
    """
    The importer recomputes only its own rows, with progress after each batch.

    The full sweep stays the backfill (`just recompute-renovation-plan`); a load
    of ~20k rows must not rewrite the ~306k-row plan.
    """
    plan = (await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    rows = list(
        (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    first, second = rows[0], rows[1]
    first.work_duration_days = None
    second.work_duration_days = None
    await db_session.flush()

    points: list[int] = []
    result = await recompute_plan_rows(
        db_session,
        plan_id=plan.id,
        row_ids=[first.id],
        progress=points.append,
    )
    await db_session.flush()

    assert (result.scanned, result.changed) == (1, 1)
    assert points == [1]
    untouched = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.id) == second.id))).one()
    # The untouched row keeps its cleared value: the narrowed sweep read only
    # the named row.
    assert untouched.work_duration_days is None

    rest = await recompute_plan_rows(db_session, plan_id=plan.id, row_ids=[second.id])
    assert (rest.scanned, rest.changed) == (1, 1)
