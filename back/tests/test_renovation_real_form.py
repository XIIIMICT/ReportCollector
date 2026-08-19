"""Интеграционный тест: движок формул и ID-генератор на реальной легаси-форме."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.renovation.calculations import FormulaDefinition, FormulaEngine, FormulaError
from src.renovation.renovation_id import RenovationIdInput, calculate_renovation_id

FORM_PATH = Path(__file__).parent / "test_data" / "renovation-form.json"

pytestmark = pytest.mark.skipif(not FORM_PATH.exists(), reason="легаси renovation-form.json отсутствует")


@pytest.fixture(scope="module")
def form() -> dict:
    return json.loads(FORM_PATH.read_text(encoding="utf-8"))


def _compile_real_engine(form: dict) -> FormulaEngine:
    definitions = [
        FormulaDefinition(
            field=field["dbName"],
            cell_type=field["cellType"],
            formula=field.get("formula"),
            is_calculated=field["isCalculated"],
            decimal_places=field.get("decimals"),
        )
        for field in form["fields"]
    ]
    return FormulaEngine(definitions)


def test_real_form_compiles_all_formulas(form: dict) -> None:
    engine = _compile_real_engine(form)
    engine.materialize({})
    engine.materialize({"col_024": date(2027, 1, 1)})


def test_real_form_formula_chain_materializes_sample_row(form: dict) -> None:
    engine = _compile_real_engine(form)
    values: dict[str, object] = {
        "col_024": date(2027, 1, 1),
        "col_025": date(2027, 2, 1),
        "col_028": Decimal(10),
        "col_029": Decimal(20),
        "col_030": Decimal(30),
    }
    engine.materialize(values)
    assert values["col_026"] == Decimal(31)
    assert values["col_031"] == Decimal(60)
    assert values["col_138"] == Decimal(0)
    assert values["col_085"] == Decimal(0)
    assert values["col_032"] == Decimal(0)


def test_real_form_recalculates_after_edits(form: dict) -> None:
    engine = _compile_real_engine(form)
    values: dict[str, object] = {
        "col_024": date(2027, 1, 1),
        "col_025": date(2027, 1, 10),
        "col_092": Decimal(7),
        "col_093": Decimal(3),
        "col_095": Decimal(1),
        "col_096": Decimal(2),
    }
    engine.materialize(values)
    assert values["col_026"] == Decimal(9)
    assert values["col_091"] == Decimal(10)
    assert values["col_094"] == Decimal(3)
    assert values["col_088"] == Decimal(13)


def test_real_form_missing_inputs_leave_totals_zero(form: dict) -> None:
    engine = _compile_real_engine(form)
    values: dict[str, object] = {}
    engine.materialize(values)
    assert "col_026" not in values
    assert values["col_138"] == Decimal(0)


def test_real_form_date_formula_shifts_plan_date(form: dict) -> None:
    engine = _compile_real_engine(form)
    values: dict[str, object] = {"col_024": date(2027, 2, 15)}
    engine.materialize(values)
    assert values["col_203"] == date(2027, 1, 16)


def test_real_form_id_materializes_documented_sample(form: dict) -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            start_date="2027-01-01",
            asset_repair_code="BSKR",
            direction_code="1.1.1.",
            defect_number="1382467",
        )
    )
    assert result.value == "2027_BSKR_1.1.1._1382467_00"


def test_real_form_id_uses_null_placeholders_when_components_missing(form: dict) -> None:
    result = calculate_renovation_id(RenovationIdInput(start_date="2027-01-01"))
    assert result.value == "2027_<NULL>_<NULL>_<NULL>*<NULL>*Январь_00"
    assert result.missing_components == (
        "asset_repair_code",
        "direction_code",
        "technology_position",
        "work_type_label",
    )


def test_real_form_rejects_a_formula_cycle(form: dict) -> None:
    definitions = [
        FormulaDefinition(
            field=field["dbName"],
            cell_type=field["cellType"],
            formula=field.get("formula"),
            is_calculated=field["isCalculated"],
        )
        for field in form["fields"]
    ]
    definitions.append(
        FormulaDefinition(
            field="col_901",
            cell_type="number",
            formula="col_902 + 1",
            is_calculated=True,
        )
    )
    definitions.append(
        FormulaDefinition(
            field="col_902",
            cell_type="number",
            formula="col_901 + 1",
            is_calculated=True,
        )
    )
    with pytest.raises(FormulaError, match="обнаружен цикл расчётных полей"):
        FormulaEngine(definitions)
