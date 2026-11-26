"""Decimal arithmetic acceptance tests for the renovation formula engine.

The legacy ``formula.rs`` is not present in this repository. The expected values
below are derived from the documented legacy contract (f64 literals limited to
the finite range, integer-day date math, rounding to the declared precision) and
from the repository's money convention (``ROUND_HALF_UP``). See docs/DATA-JOBS.md,
«Renovation formula arithmetic».
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.renovation.calculations import (
    DATE_CELL,
    NUMBER_CELL,
    FormulaDefinition,
    FormulaEngine,
    FormulaError,
)
from src.renovation.form_schema import field_metas, formula_engine

FORM_PATH = Path(__file__).parents[1] / "src" / "renovation" / "form_data" / "renovation-form.json"


def _number(
    field: str,
    formula: str | None = None,
    *,
    calculated: bool = False,
    places: int | None = None,
) -> FormulaDefinition:
    return FormulaDefinition(
        field=field,
        cell_type=NUMBER_CELL,
        formula=formula,
        is_calculated=calculated,
        decimal_places=places,
    )


def _date(field: str, formula: str | None = None, *, calculated: bool = False) -> FormulaDefinition:
    return FormulaDefinition(field=field, cell_type=DATE_CELL, formula=formula, is_calculated=calculated)


def _engine(*definitions: FormulaDefinition) -> FormulaEngine:
    return FormulaEngine(list(definitions))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.005"), Decimal("1.01")),
        (Decimal("1.015"), Decimal("1.02")),
        (Decimal("-1.005"), Decimal("-1.01")),
        (Decimal("-1.015"), Decimal("-1.02")),
        (Decimal("0.005"), Decimal("0.01")),
        (Decimal("-0.005"), Decimal("-0.01")),
    ],
)
def test_decimal_midpoints_round_half_away_from_zero(value: Decimal, expected: Decimal) -> None:
    engine = _engine(_number("src"), _number("out", "src", calculated=True, places=2))
    values: dict[str, object] = {"src": value}
    engine.materialize(values)
    assert values["out"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("0.5"), Decimal(1)), (Decimal("1.5"), Decimal(2)), (Decimal("-0.5"), Decimal(-1))],
)
def test_integer_midpoints_are_not_bankers_rounding(value: Decimal, expected: Decimal) -> None:
    engine = _engine(_number("src"), _number("out", "src", calculated=True, places=0))
    values: dict[str, object] = {"src": value}
    engine.materialize(values)
    assert values["out"] == expected


def test_decimal_add_chain_has_no_binary_artifact() -> None:
    engine = _engine(
        _number("a"),
        _number("b"),
        _number("c"),
        _number("out", "a + b + c", calculated=True),
    )
    values: dict[str, object] = {"a": Decimal("0.1"), "b": Decimal("0.2"), "c": Decimal("0.3")}
    engine.materialize(values)
    assert values["out"] == Decimal("0.6")


def test_decimal_multiply_has_no_binary_artifact() -> None:
    engine = _engine(
        _number("a"),
        _number("b"),
        _number("out", "a * b", calculated=True),
    )
    values: dict[str, object] = {"a": Decimal("1.1"), "b": Decimal("1.1")}
    engine.materialize(values)
    assert values["out"] == Decimal("1.21")


def test_decimal_literals_are_parsed_exactly() -> None:
    engine = _engine(_number("out", "0.1 + 0.2 + 1e-2", calculated=True))
    values: dict[str, object] = {}
    engine.materialize(values)
    assert values["out"] == Decimal("0.31")


def test_division_uses_documented_28_digit_precision() -> None:
    engine = _engine(
        _number("a"),
        _number("b"),
        _number("quotient", "a / b", calculated=True),
        _number("roundtrip", "a / b * b", calculated=True),
    )
    values: dict[str, object] = {"a": Decimal(1), "b": Decimal(3)}
    engine.materialize(values)
    assert values["quotient"] == Decimal("0.3333333333333333333333333333")
    assert values["roundtrip"] == Decimal("0.9999999999999999999999999999")


def test_exact_division_stays_exact() -> None:
    engine = _engine(
        _number("a"),
        _number("b"),
        _number("out", "a / b", calculated=True),
    )
    values: dict[str, object] = {"a": Decimal(10), "b": Decimal(4)}
    engine.materialize(values)
    assert values["out"] == Decimal("2.5")


def test_negative_zero_is_normalized_at_materialization() -> None:
    engine = _engine(
        _number("src"),
        _number("out", "-src + src", calculated=True, places=2),
        _number("raw", "-src + src", calculated=True),
    )
    values: dict[str, object] = {"src": Decimal(5)}
    engine.materialize(values)
    for field in ("out", "raw"):
        result = values[field]
        assert isinstance(result, Decimal)
        assert result == 0
        assert not result.is_signed()
        assert str(result) == "0"


def test_date_shifts_are_exact_with_integral_decimal_operands() -> None:
    engine = _engine(
        _date("start"),
        _date("plus", "start + 30", calculated=True),
        _date("minus", "start - 30", calculated=True),
        _date("fractional", "start + 1.0", calculated=True),
    )
    values: dict[str, object] = {"start": date(2027, 1, 16)}
    engine.materialize(values)
    assert values["plus"] == date(2027, 2, 15)
    assert values["minus"] == date(2026, 12, 17)
    assert values["fractional"] == date(2027, 1, 17)


def test_date_difference_is_an_exact_integer_day_count() -> None:
    engine = _engine(
        _date("later"),
        _date("earlier"),
        _number("days", "later - earlier", calculated=True),
    )
    values: dict[str, object] = {"later": date(2027, 2, 15), "earlier": date(2026, 12, 31)}
    engine.materialize(values)
    assert values["days"] == Decimal(46)


def test_out_of_range_date_shift_is_a_formula_error() -> None:
    engine = _engine(_date("start"), _date("out", "start + 1e9", calculated=True))
    with pytest.raises(FormulaError, match="выходит за допустимый диапазон"):
        engine.materialize({"start": date(2027, 1, 1)})


def test_fractional_date_shift_is_still_rejected() -> None:
    engine = _engine(_date("start"), _date("out", "start + 1.5", calculated=True))
    with pytest.raises(FormulaError, match="целое количество дней"):
        engine.materialize({"start": date(2027, 1, 1)})


@pytest.mark.parametrize("denominator", [Decimal(0), None])
def test_division_by_zero_is_deterministic(denominator: Decimal | None) -> None:
    engine = _engine(
        _number("a"),
        _number("b"),
        _number("out", "a / b", calculated=True),
    )
    values: dict[str, object] = {"a": Decimal(1)}
    if denominator is not None:
        values["b"] = denominator
    with pytest.raises(FormulaError, match="деление на ноль"):
        engine.materialize(values)


@pytest.mark.parametrize("value", [Decimal("Infinity"), Decimal("NaN"), float("inf")])
def test_non_finite_number_input_is_deterministic(value: object) -> None:
    engine = _engine(_number("src"), _number("out", "src + 1", calculated=True))
    with pytest.raises(FormulaError, match="выходит за допустимый диапазон"):
        engine.materialize({"src": value})


def test_non_finite_literal_is_rejected_at_compile_time() -> None:
    with pytest.raises(FormulaError, match="числовой литерал должен быть конечным"):
        _engine(_number("out", "1e999", calculated=True))


def test_full_form_graph_has_133_formulas() -> None:
    form = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    declared = [field for field in form["fields"] if field.get("formula")]
    assert len(declared) == 133
    targets = {meta.db_name for meta in field_metas() if meta.is_calculated and meta.formula is not None}
    engine = formula_engine()
    assert len(targets) == 133
    all_fields = [meta.db_name for meta in field_metas()]
    assert targets <= engine.affected_fields(all_fields)


def test_full_form_graph_materializes_financial_and_monthly_chains() -> None:
    form = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    assert len([field for field in form["fields"] if field.get("formula")]) == 133
    targets = {meta.db_name for meta in field_metas() if meta.is_calculated and meta.formula is not None}
    formula_less = {meta.db_name for meta in field_metas() if meta.is_calculated and meta.formula is None}

    inputs: dict[str, object] = {
        "work_start_date": date(2027, 1, 1),
        "work_end_date": date(2027, 1, 31),
        "work_cost_rub_ex_vat": Decimal("100.004"),
        "contractor_supplied_materials_cost_rub_ex_vat": Decimal("1.001"),
        "customer_materials_cost_rub_ex_vat": Decimal("2.000"),
        "plan_adjustment_january_cw_cost_k_rub": Decimal("1.005"),
        "plan_adjustment_january_cm_cost_k_rub": Decimal("2.005"),
        "centralized_supply_materials_cost_thousand_rub_ex_vat": Decimal("0.1"),
        "direct_supply_materials_cost_thousand_rub_ex_vat": Decimal("0.2"),
        "contractor_supply_materials_cost_thousand_rub_ex_vat": Decimal("0.3"),
    }
    values = dict(inputs)
    formula_engine().materialize(values)

    assert targets <= set(values)
    assert not formula_less & set(values)
    assert len(values) == len(inputs) + len(targets)
    for field in targets:
        assert isinstance(values[field], (Decimal, date)), f"{field} materialized as {type(values[field])}"

    # Financial total: 100.004 + 1.001 + 2.000 = 103.005 -> 103.01 (half up).
    assert values["total_cost_rub_ex_vat"] == Decimal("103.01")
    # Monthly roll-up: the January cells gate on YEAR/MONTH of the work end date.
    assert values["original_plan_january_cw_cost_k_rub"] == Decimal("101.01")
    assert values["original_plan_january_cm_cost_k_rub"] == Decimal("2.00")
    assert values["original_plan_january_total_cost_thousand_rub_ex_vat"] == Decimal("103.01")
    assert values["plan_adjustment_january_total_cost_thousand_rub_ex_vat"] == Decimal("3.01")
    assert values["plan_adjustment_total_cost_thousand_rub_ex_vat"] == Decimal("3.01")
    assert values["adj_january_total_cost_k_rub"] == Decimal("106.02")
    assert values["plan_after_proposed_adjustment_total_cost_thousand_rub_ex_vat"] == Decimal("106.02")
    assert values["total_materials_cost"] == Decimal("0.60")
    assert values["work_duration_days"] == Decimal(30)
    assert values["required_materials_delivery_date"] == date(2026, 12, 2)
