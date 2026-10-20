"""Calendar scope, cost import and editable КППК form contracts."""

from datetime import date
from decimal import Decimal
from io import BytesIO

from conftest import TEST_SCHEMA, signed_identity_headers
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.kppk.models import KppkCost, KppkOrg, KppkProductionPlan, KppkRealization, KppkRealizationPlan
from src.scripts.seed_kppk import _rebuild_costs

HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


async def test_cost_import_selects_selling_markets_per_slice(db_session: AsyncSession) -> None:
    """Zero-volume markets may carry stale prices; BP/OP choose independently."""
    plan_rows = list((await db_session.exec(select(KppkRealizationPlan))).all())
    for row in plan_rows:
        row.month_bp_plan = Decimal(0)
        row.month_max_plan = Decimal(0)
    selling, inactive = [row for row in plan_rows if row.product == "Сода кальцинированная марки Б"]
    selling.month_bp_plan = Decimal(5)
    selling.input_cost_bp = Decimal(100)
    selling.var_exp_bp_plan = Decimal(60)
    inactive.input_cost_bp = Decimal(999)
    inactive.var_exp_bp_plan = Decimal(888)
    # The operating slice sells into the other market instead.
    inactive.month_max_plan = Decimal(2)
    inactive.input_cost_plan = Decimal(120)
    inactive.var_exp_plan = Decimal(70)
    selling.input_cost_plan = Decimal(777)
    selling.var_exp_plan = Decimal(666)
    db_session.add(
        KppkProductionPlan(
            org_id=selling.org_id,
            period_date=date(2026, 6, 1),
            rep_date=date(2026, 6, 30),
            product=selling.product,
            full_cost_bp=Decimal(150),
            full_cost_plan=Decimal(170),
        )
    )
    await db_session.flush()
    connection = await db_session.connection()
    await _rebuild_costs(connection, TEST_SCHEMA)
    row = (await db_session.exec(select(KppkCost).where(col(KppkCost.product) == selling.product))).one()
    assert (row.production_cost_bp, row.variable_cost_bp) == (Decimal(100), Decimal(60))
    assert (row.production_cost_op, row.variable_cost_op) == (Decimal(120), Decimal(70))
    assert (row.full_cost_bp, row.full_cost_op) == (Decimal(150), Decimal(170))


async def test_cost_import_keeps_no_sales_production_and_reports_conflicts(db_session: AsyncSession, capsys) -> None:
    rows = list((await db_session.exec(select(KppkRealizationPlan))).all())
    product_rows = [row for row in rows if row.product == "Сода кальцинированная марки Б"]
    for row in product_rows:
        row.month_bp_plan = Decimal(0)
        row.input_cost_bp = Decimal(25)
    await db_session.flush()
    connection = await db_session.connection()
    await _rebuild_costs(connection, TEST_SCHEMA)
    cost = (await db_session.exec(select(KppkCost).where(col(KppkCost.product) == product_rows[0].product))).one()
    assert cost.production_cost_bp == Decimal(25)
    product_rows[0].month_bp_plan = Decimal(1)
    product_rows[1].month_bp_plan = Decimal(2)
    product_rows[1].input_cost_bp = Decimal(26)
    product_rows[0].var_exp_bp_plan = Decimal(50)
    product_rows[1].var_exp_bp_plan = Decimal(51)
    await db_session.flush()
    assert await _rebuild_costs(connection, TEST_SCHEMA) > 0
    cost = (await db_session.exec(select(KppkCost).where(col(KppkCost.product) == product_rows[0].product))).one()
    assert cost.production_cost_bp == Decimal(26)
    assert cost.variable_cost_bp == Decimal(51)
    warnings = capsys.readouterr().out
    assert warnings.count("Предупреждение:") == 2
    assert "kppk_realization_plan.input_cost_bp" in warnings
    assert "kppk_realization_plan.var_exp_bp_plan" in warnings
    assert "максимум 26" in warnings
    assert "максимум 51" in warnings


async def test_cost_api_number_order_scope_edit_undo_and_export(client: AsyncClient, db_session: AsyncSession) -> None:
    org = (await db_session.exec(select(KppkOrg).where(col(KppkOrg.num) == 2))).one()
    for name in ("Сода кальцинированная марки Б", "Сульфат натрия"):
        db_session.add(KppkCost(org_id=org.id, period_date=date(2026, 6, 1), product=name, full_cost_bp=Decimal(15)))
    await db_session.commit()
    params = {"orgId": str(org.id), "month": 6, "year": 2026}
    response = await client.get("/api/kppk/cost", params=params, headers=HEADERS)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["period"]["periodDate"] == "2026-06-01"
    assert [row["productNum"] for row in view["rows"]] == [1, 2]
    assert "region" not in view["rows"][0]
    assert "exciseBp" not in view["rows"][0]
    assert "exciseFact" not in view["rows"][0]
    row_id = view["rows"][0]["id"]
    response = await client.patch(
        f"/api/kppk/cost/{row_id}?session_id=cost", headers=HEADERS, json={"field": "fullCostBp", "value": "22,5"}
    )
    assert response.status_code == 200, response.text
    response = await client.post("/api/kppk/undo?session_id=cost", headers=HEADERS, json={"section": "kppkCost"})
    assert response.json()["applied"] is True, response.text
    response = await client.post(
        "/api/kppk/cost/export",
        headers=HEADERS,
        json={
            **params,
            "slice": "bp",
            "fileName": "cost.xlsx",
            "rowIds": [view["rows"][1]["id"], row_id],
            "columns": [
                {"key": "productNum", "headerPath": ["№"], "width": 10},
                {"key": "fullCostBp", "headerPath": ["Полная себестоимость"], "width": 25},
            ],
        },
    )
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    assert not sheet.merged_cells.ranges
    assert list(sheet.values)[1:] == [(2, 15), (1, 15)]
    response = await client.get("/api/kppk/cost", params={**params, "year": 2027}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["rows"] == []


async def test_calendar_schema_and_zero_numeric_cells(client: AsyncClient, db_session: AsyncSession) -> None:
    connection = await db_session.connection()
    assert (await connection.execute(text("SELECT to_regclass(current_schema() || '.kppk_periods')"))).scalar() is None
    row = (await db_session.exec(select(KppkRealization))).first()
    assert row is not None
    for field in ("buyoutFact", "fact", "exciseFact"):
        response = await client.patch(
            f"/api/kppk/realization/{row.id}?slice=fact&session_id=zero",
            headers=HEADERS,
            json={"field": field, "value": None},
        )
        assert response.status_code == 200, response.text
        assert response.json()["row"][field] == 0
    response = await client.get(
        "/api/kppk/realization",
        headers=HEADERS,
        params={"orgId": str(row.org_id), "slice": "fact", "month": 2, "year": 2028},
    )
    assert response.status_code == 200, response.text
    assert response.json()["period"]["repDate"] == "2028-02-29"
