"""Compatibility contract of ``GET /plan``: bounded full read, paged alternative, uncapped export rowIds."""

from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.export import ERROR_DUPLICATE_ROWS
from src.renovation import plan_reads
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.plan_reads import FULL_PLAN_READ_MAX_ROWS
from src.renovation.schemas import PLAN_FULL_READ_ERROR_MESSAGE, RenovationPlanRowRead

if TYPE_CHECKING:
    from src.renovation.models import RAsset

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


async def _working_plan(session: AsyncSession) -> RenovationPlan:
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    return plan


async def _plan_row_count(session: AsyncSession, plan_id: UUID) -> int:
    return int(
        (
            await session.exec(
                select(func.count()).select_from(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan_id)
            )
        ).one()
    )


async def _add_sparse_rows(session: AsyncSession, plan_id: UUID, count: int, *, first_position: int) -> None:
    """Add `count` draft rows; only a unique work code is filled (the plan is sparse)."""
    now = datetime.now(UTC)
    session.add_all(
        RenovationPlanRow(
            plan_id=plan_id,
            asset_id=None,
            position=position,
            work_or_repair_code=f"compat-{position}",
            created_at=now,
            updated_at=now,
            baseline_generation=1,
        )
        for position in range(first_position, first_position + count)
    )
    # The request dependency rolls the session back on a 422 response, so the
    # rows must be committed to the fixture's savepoint before that request.
    await session.commit()


async def test_full_plan_read_at_budget_boundary_is_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    """Exactly `FULL_PLAN_READ_MAX_ROWS` rows still get the historical full response."""
    plan = await _working_plan(db_session)
    existing = await _plan_row_count(db_session, plan.id)
    await _add_sparse_rows(db_session, plan.id, FULL_PLAN_READ_MAX_ROWS - existing, first_position=existing + 1)

    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == FULL_PLAN_READ_MAX_ROWS


async def test_full_plan_read_above_budget_is_refused_without_materializing_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    """A large plan cannot force unbounded full response materialization or a silent slice."""
    plan = await _working_plan(db_session)
    existing = await _plan_row_count(db_session, plan.id)
    await _add_sparse_rows(db_session, plan.id, FULL_PLAN_READ_MAX_ROWS + 1 - existing, first_position=existing + 1)

    materialized = 0
    real_row_read = plan_reads._plan_row_read

    def counting_row_read(
        row: RenovationPlanRow,
        asset: RAsset | None,
        label_map: dict[UUID, str],
    ) -> RenovationPlanRowRead:
        nonlocal materialized
        materialized += 1
        return real_row_read(row, asset, label_map)

    monkeypatch.setattr(plan_reads, "_plan_row_read", counting_row_read)

    response = await client.get("/api/renovation/plan", headers=ADMIN_HEADERS)
    assert materialized == 0, "the refused plan must never be converted into response row models"
    assert response.status_code == 422
    body = response.json()
    assert body == {
        "detail": {
            "code": "PLAN_FULL_READ_TOO_LARGE",
            "message": PLAN_FULL_READ_ERROR_MESSAGE.format(max_rows=FULL_PLAN_READ_MAX_ROWS),
            "maxRows": FULL_PLAN_READ_MAX_ROWS,
            "alternative": "/plan/rows",
        }
    }

    # The documented alternative still serves the same oversized plan, paged.
    paged = await client.get("/api/renovation/plan/rows?offset=0&limit=100", headers=ADMIN_HEADERS)
    assert paged.status_code == 200, paged.text
    assert paged.json()["total"] == FULL_PLAN_READ_MAX_ROWS + 1
    assert len(paged.json()["rows"]) == 100


async def test_plan_export_exact_row_ids_have_no_count_cap(client: AsyncClient) -> None:
    """The owner-approved exact-set export accepts an unlimited number of IDs."""
    rows = (await client.get("/api/renovation/plan/rows?limit=1", headers=ADMIN_HEADERS)).json()["rows"]
    assert len(rows) == 1
    ids = [UUID(rows[0]["id"])]
    ids.extend(uuid4() for _ in range(FULL_PLAN_READ_MAX_ROWS))

    response = await client.post(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        json={"rowIds": [str(row_id) for row_id in ids]},
    )
    assert response.status_code == 200, response.text
    # Only the one existing ID is written; the 4 GiB ceiling is unchanged.
    worksheet = load_workbook(BytesIO(response.content)).active
    assert worksheet.max_row == 14


async def test_plan_export_duplicate_row_ids_still_rejected(client: AsyncClient) -> None:
    """Duplicates are refused even though the ID count itself is uncapped."""
    row_id = (await client.get("/api/renovation/plan/rows?limit=1", headers=ADMIN_HEADERS)).json()["rows"][0]["id"]
    response = await client.post(
        "/api/renovation/plan/export",
        headers=ADMIN_HEADERS,
        json={"rowIds": [row_id, row_id]},
    )
    assert response.status_code == 422
    assert ERROR_DUPLICATE_ROWS in response.text
