"""Тесты порта движка расчётных формул (эталон: formula.rs)."""

from datetime import date
from decimal import Decimal

import pytest

from src.renovation.calculations import (
    DATE_CELL,
    NUMBER_CELL,
    FormulaDefinition,
    FormulaEngine,
    FormulaError,
)
from src.renovation.form_schema import formula_engine


def test_app_materialization_preserves_editable_work_code() -> None:
    values: dict[str, object] = {
        "work_or_repair_code": "2027_SODA_1.1.1_1234567",
        "work_start_date": date(2027, 1, 10),
        "work_end_date": date(2027, 1, 25),
    }
    formula_engine().materialize(values)
    assert values.get("work_or_repair_code") == "2027_SODA_1.1.1_1234567"
    assert values["work_duration_days"] == Decimal(15)


def number_definition(field: str, formula: str | None = None, calculated: bool = False) -> FormulaDefinition:
    return FormulaDefinition(field=field, cell_type=NUMBER_CELL, formula=formula, is_calculated=calculated)


def date_definition(field: str, formula: str | None = None, calculated: bool = False) -> FormulaDefinition:
    return FormulaDefinition(field=field, cell_type=DATE_CELL, formula=formula, is_calculated=calculated)


def compile_engine(*definitions: FormulaDefinition) -> FormulaEngine:
    return FormulaEngine(list(definitions))


def test_evaluates_formula_dag_in_dependency_order() -> None:
    engine = compile_engine(
        number_definition("col_001"),
        number_definition("col_002"),
        number_definition("col_003", "col_001 + col_002", calculated=True),
        number_definition("col_004", "col_003 + col_002", calculated=True),
    )
    values: dict[str, object] = {"col_001": Decimal(1), "col_002": Decimal(2)}
    engine.materialize(values)
    assert values["col_003"] == Decimal(3)
    assert values["col_004"] == Decimal(5)


def test_missing_number_dependency_is_zero() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_001", "col_002 + 5", calculated=True),
    )
    values: dict[str, object] = {}
    engine.materialize(values)
    assert values["col_001"] == Decimal(5)


def test_missing_date_dependency_skips_result() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002", calculated=True),
    )
    values: dict[str, object] = {}
    engine.materialize(values)
    assert "col_001" not in values


def test_date_minus_date_is_days_number() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_003"),
        number_definition("col_001", "col_002 - col_003", calculated=True),
    )
    values: dict[str, object] = {
        "col_002": date(2027, 2, 15),
        "col_003": date(2027, 1, 1),
    }
    engine.materialize(values)
    assert values["col_001"] == Decimal(45)


def test_date_minus_number_shifts_date() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002 - 30", calculated=True),
    )
    values: dict[str, object] = {"col_002": date(2027, 2, 15)}
    engine.materialize(values)
    assert values["col_001"] == date(2027, 1, 16)


def test_date_plus_number_shifts_date() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002 + 30", calculated=True),
    )
    values: dict[str, object] = {"col_002": date(2027, 1, 16)}
    engine.materialize(values)
    assert values["col_001"] == date(2027, 2, 15)


def test_negate_expression() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_001", "-col_002", calculated=True),
    )
    values: dict[str, object] = {"col_002": Decimal(7)}
    engine.materialize(values)
    assert values["col_001"] == Decimal(-7)


def test_precedence_and_parentheses() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_003"),
        number_definition("col_001", "col_002 + col_003 * 2", calculated=True),
        number_definition("col_004", "(col_002 + col_003) * 2", calculated=True),
    )
    values: dict[str, object] = {"col_002": Decimal(1), "col_003": Decimal(2)}
    engine.materialize(values)
    assert values["col_001"] == Decimal(5)
    assert values["col_004"] == Decimal(6)


def test_division_by_zero_is_an_error() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_003"),
        number_definition("col_001", "col_002 / col_003", calculated=True),
    )
    with pytest.raises(FormulaError, match="деление на ноль"):
        engine.materialize({"col_002": Decimal(1), "col_003": Decimal(0)})


def test_missing_division_by_zero_is_an_error() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_003"),
        number_definition("col_001", "col_002 / col_003", calculated=True),
    )
    with pytest.raises(FormulaError, match="деление на ноль"):
        engine.materialize({"col_002": Decimal(1)})


def test_missing_date_in_binary_skips_result() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002 - 30", calculated=True),
    )
    values: dict[str, object] = {}
    engine.materialize(values)
    assert "col_001" not in values


def test_rounding_to_decimal_places() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_003"),
        FormulaDefinition(
            field="col_001",
            cell_type=NUMBER_CELL,
            formula="col_002 + col_003",
            is_calculated=True,
            decimal_places=2,
        ),
    )
    values: dict[str, object] = {"col_002": Decimal("1.006"), "col_003": Decimal(0)}
    engine.materialize(values)
    assert values["col_001"] == Decimal("1.01")


def test_negative_zero_is_normalized() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        FormulaDefinition(
            field="col_001",
            cell_type=NUMBER_CELL,
            formula="-col_002 + col_002",
            is_calculated=True,
            decimal_places=2,
        ),
    )
    values: dict[str, object] = {"col_002": Decimal(5)}
    engine.materialize(values)
    assert values["col_001"] == Decimal(0)


def test_removes_calculated_fields_without_formula() -> None:
    engine = compile_engine(
        number_definition("col_001", None, calculated=True),
        number_definition("col_003"),
        number_definition("col_002", "col_003 + 1", calculated=True),
    )
    values: dict[str, object] = {"col_001": Decimal(1), "col_003": Decimal(2)}
    engine.materialize(values)
    assert "col_001" not in values
    assert values["col_002"] == Decimal(3)


def test_rejects_unknown_dependency() -> None:
    with pytest.raises(FormulaError, match="ссылается на неизвестное поле"):
        compile_engine(number_definition("col_001", "missing + 1", calculated=True))


def test_rejects_unsupported_dependency_type() -> None:
    with pytest.raises(FormulaError, match="ссылается на неподдерживаемый тип поля"):
        compile_engine(
            FormulaDefinition(field="col_001", cell_type="text", formula=None, is_calculated=False),
            number_definition("col_002", "col_001 + 1", calculated=True),
        )


def test_rejects_unsupported_target_type() -> None:
    with pytest.raises(FormulaError, match="имеет неподдерживаемый тип для формулы"):
        compile_engine(FormulaDefinition(field="col_001", cell_type="text", formula="1 + 1", is_calculated=True))


def test_rejects_duplicate_fields() -> None:
    with pytest.raises(FormulaError, match="определено более одного раза"):
        compile_engine(number_definition("col_001"), number_definition("col_001"))


def test_rejects_cycles() -> None:
    with pytest.raises(FormulaError, match="обнаружен цикл расчётных полей"):
        compile_engine(
            number_definition("col_001", "col_002 + 1", calculated=True),
            number_definition("col_002", "col_001 + 1", calculated=True),
        )


def test_rejects_non_ascii_formula() -> None:
    with pytest.raises(FormulaError, match="допускаются только ASCII"):
        compile_engine(number_definition("col_001", "col_002 + ф", calculated=True))


def test_rejects_malformed_formula() -> None:
    with pytest.raises(FormulaError, match="не закрыта скобка"):
        compile_engine(number_definition("col_001", "(col_002 + 1", calculated=True))
    with pytest.raises(FormulaError, match="ожидалось число или имя поля"):
        compile_engine(number_definition("col_001", "col_002 +", calculated=True))
    with pytest.raises(FormulaError, match="некорректное число"):
        compile_engine(number_definition("col_001", "col_002 + .", calculated=True))
    with pytest.raises(FormulaError, match="в экспоненте отсутствуют цифры"):
        compile_engine(number_definition("col_001", "col_002 + 1e", calculated=True))
    with pytest.raises(FormulaError, match="числовой литерал должен быть конечным"):
        compile_engine(number_definition("col_001", "col_002 + 1e999", calculated=True))
    with pytest.raises(FormulaError, match="неподдерживаемый символ"):
        compile_engine(number_definition("col_001", "col_002 $ 1", calculated=True))


def test_invalid_value_types_are_errors() -> None:
    engine = compile_engine(
        number_definition("col_002"),
        number_definition("col_001", "col_002 + 1", calculated=True),
    )
    with pytest.raises(FormulaError, match="содержит значение неверного типа"):
        engine.materialize({"col_002": "text"})


def test_invalid_date_is_an_error() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002", calculated=True),
    )
    with pytest.raises(FormulaError, match="содержит некорректную дату"):
        engine.materialize({"col_002": "not-a-date"})


def test_invalid_operation_is_an_error() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_003"),
        number_definition("col_001", "col_002 + col_003", calculated=True),
    )
    with pytest.raises(FormulaError, match="не поддерживается"):
        engine.materialize({"col_002": date(2027, 1, 1), "col_003": date(2027, 1, 2)})


def test_target_type_mismatch_is_an_error() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_003"),
        date_definition("col_001", "col_002 - col_003", calculated=True),
    )
    with pytest.raises(FormulaError, match="не соответствует типу поля"):
        engine.materialize({"col_002": date(2027, 2, 15), "col_003": date(2027, 1, 1)})


def test_fractional_days_are_rejected() -> None:
    engine = compile_engine(
        date_definition("col_002"),
        date_definition("col_001", "col_002 + 1.5", calculated=True),
    )
    with pytest.raises(FormulaError, match="целое количество дней"):
        engine.materialize({"col_002": date(2027, 1, 1)})
