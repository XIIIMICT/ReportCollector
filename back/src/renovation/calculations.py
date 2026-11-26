"""
Port of the renovation formula engine.

Reference: ReportCollector-Renovation/src-tauri/src/formula.rs (not present in
this repository). Numeric values stay ``Decimal`` from literal/input parsing
through evaluation and materialization; dates stay ``date`` with integer-day
shifts.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

NUMBER_CELL = "number"
DATE_CELL = "date"

# 28 significant digits covers NUMERIC(18,4) with headroom for chained sums.
_ARITHMETIC_PRECISION = 28
# Literal magnitude still observes the legacy f64 finite-literal guard (~1e308).
_MAX_LITERAL_EXPONENT = 308
# timedelta.max.days; larger shifts must fail as FormulaError, not OverflowError.
_MAX_DATE_SHIFT = 999_999_999
_ARITHMETIC_CONTEXT = Context(
    prec=_ARITHMETIC_PRECISION,
    rounding=ROUND_HALF_UP,
    traps=[DivisionByZero, Overflow, InvalidOperation],
)

_ERR_PARSE = "формула поля {field}: {detail}"
_ERR_DUPLICATE_FIELD = "поле {field} определено более одного раза"
_ERR_UNKNOWN_DEPENDENCY = "формула {field} ссылается на неизвестное поле {dependency}"
_ERR_UNSUPPORTED_DEPENDENCY = "формула {field} ссылается на неподдерживаемый тип поля {dependency}"
_ERR_CYCLE = "обнаружен цикл расчётных полей: {path}"
_ERR_UNSUPPORTED_TARGET = "поле {field} имеет неподдерживаемый тип для формулы"
_ERR_INVALID_VALUE_TYPE = "поле {field} содержит значение неверного типа"
_ERR_INVALID_DATE = "поле {field} содержит некорректную дату"
_ERR_DIVISION_BY_ZERO = "деление на ноль"
_ERR_INVALID_OPERATION = "операция {operation} не поддерживается для этих типов"
_ERR_FRACTIONAL_DAYS = "арифметика даты требует целое количество дней"
_ERR_OVERFLOW = "результат вычисления выходит за допустимый диапазон"
_ERR_TARGET_TYPE_MISMATCH = "результат формулы {field} не соответствует типу поля"

_PARSE_ASCII_ONLY = "допускаются только ASCII-идентификаторы и операторы"
_PARSE_UNEXPECTED_CHAR = "неподдерживаемый символ в позиции {position}"
_PARSE_UNCLOSED_PAREN = "не закрыта скобка"
_PARSE_EXPECTED_VALUE = "ожидалось число или имя поля"
_PARSE_INVALID_NUMBER = "некорректное число"
_PARSE_EXPONENT_DIGITS = "в экспоненте отсутствуют цифры"
_PARSE_FINITE_LITERAL = "числовой литерал должен быть конечным"

_UNARY_NEGATE_LABEL = "-x"
_UNKNOWN_VALUE_LABEL = "?"
_COMPARE_LABEL = "="
_DATE_LABEL = "date"


class FormulaError(ValueError):
    """Ошибка компиляции или вычисления формулы."""


class _ParserError(ValueError):
    """Детальная ошибка парсинга выражения (поле добавляется при компиляции)."""


def _parse_error(field: str, detail: str) -> FormulaError:
    return FormulaError(_ERR_PARSE.format(field=field, detail=detail))


def _duplicate_field(field: str) -> FormulaError:
    return FormulaError(_ERR_DUPLICATE_FIELD.format(field=field))


def _unknown_dependency(field: str, dependency: str) -> FormulaError:
    return FormulaError(_ERR_UNKNOWN_DEPENDENCY.format(field=field, dependency=dependency))


def _unsupported_dependency(field: str, dependency: str) -> FormulaError:
    return FormulaError(_ERR_UNSUPPORTED_DEPENDENCY.format(field=field, dependency=dependency))


def _cycle(path: str) -> FormulaError:
    return FormulaError(_ERR_CYCLE.format(path=path))


def _unsupported_target(field: str) -> FormulaError:
    return FormulaError(_ERR_UNSUPPORTED_TARGET.format(field=field))


def _invalid_value_type(field: str) -> FormulaError:
    return FormulaError(_ERR_INVALID_VALUE_TYPE.format(field=field))


def _invalid_date(field: str) -> FormulaError:
    return FormulaError(_ERR_INVALID_DATE.format(field=field))


def _division_by_zero() -> FormulaError:
    return FormulaError(_ERR_DIVISION_BY_ZERO)


def _invalid_operation(operation: str) -> FormulaError:
    return FormulaError(_ERR_INVALID_OPERATION.format(operation=operation))


def _fractional_days() -> FormulaError:
    return FormulaError(_ERR_FRACTIONAL_DAYS)


def _overflow() -> FormulaError:
    return FormulaError(_ERR_OVERFLOW)


def _target_type_mismatch(field: str) -> FormulaError:
    return FormulaError(_ERR_TARGET_TYPE_MISMATCH.format(field=field))


@dataclass(frozen=True)
class FormulaDefinition:
    """Спецификация расчётного поля из описания формы."""

    field: str
    cell_type: str
    formula: str | None
    is_calculated: bool
    decimal_places: int | None = None


@dataclass(frozen=True)
class _Expression:
    pass


@dataclass(frozen=True)
class _Number(_Expression):
    value: Decimal


@dataclass(frozen=True)
class _Variable(_Expression):
    name: str


@dataclass(frozen=True)
class _Negate(_Expression):
    inner: _Expression


@dataclass(frozen=True)
class _Binary(_Expression):
    operator: str
    left: _Expression
    right: _Expression


@dataclass(frozen=True)
class _Compare(_Expression):
    """Equality of two expressions — evaluates to 1 (true) or 0 (false)."""

    left: _Expression
    right: _Expression


@dataclass(frozen=True)
class _Call(_Expression):
    """A function call (`IF`, `AND`, `YEAR`, `MONTH`)."""

    name: str
    args: tuple[_Expression, ...]


@dataclass(frozen=True)
class _Evaluated:
    pass


@dataclass(frozen=True)
class _EvaluatedNumber(_Evaluated):
    value: Decimal


@dataclass(frozen=True)
class _EvaluatedDate(_Evaluated):
    value: date


@dataclass(frozen=True)
class _EvaluatedMissingDate(_Evaluated):
    pass


class FormulaEngine:
    """Движок расчётных формул: компиляция и материализация значений строки."""

    def __init__(self, definitions: list[FormulaDefinition]) -> None:
        field_types: dict[str, str] = {}
        calculated_fields: list[str] = []
        for definition in definitions:
            if definition.field in field_types:
                raise _duplicate_field(definition.field)
            field_types[definition.field] = definition.cell_type
            if definition.is_calculated:
                calculated_fields.append(definition.field)

        formulas: dict[str, tuple[str, _Expression, int | None]] = {}
        for definition in definitions:
            source = definition.formula
            if source is None:
                continue
            if not definition.is_calculated:
                raise _unsupported_target(definition.field)
            if definition.cell_type not in (NUMBER_CELL, DATE_CELL):
                raise _unsupported_target(definition.field)
            try:
                expression = _Parser(source).parse()
            except _ParserError as error:
                raise _parse_error(definition.field, str(error)) from error
            dependencies = _collect_variables(expression)
            for dependency in dependencies:
                dependency_type = field_types.get(dependency)
                if dependency_type is None:
                    raise _unknown_dependency(definition.field, dependency)
                if dependency_type not in (NUMBER_CELL, DATE_CELL):
                    raise _unsupported_dependency(definition.field, dependency)
            formulas[definition.field] = (
                definition.cell_type,
                expression,
                definition.decimal_places,
            )

        order = _topological_order(formulas)
        self._field_types = field_types
        self._calculated_fields = calculated_fields
        self._calculated_set = set(calculated_fields)
        self._formulas_by_name = {
            field: (cell_type, expression, decimal_places)
            for field, (cell_type, expression, decimal_places) in formulas.items()
        }
        dependents: dict[str, set[str]] = {}
        for field, (_cell_type, expression, _decimal_places) in formulas.items():
            for dependency in _collect_variables(expression):
                dependents.setdefault(dependency, set()).add(field)
        self._dependents = dependents
        self._formulas: list[tuple[str, str, _Expression, int | None]] = []
        for field in order:
            cell_type, expression, decimal_places = formulas[field]
            self._formulas.append((field, cell_type, expression, decimal_places))

    def affected_fields(self, changed: Iterable[str]) -> set[str]:
        """Return the calculated fields that transitively depend on `changed`."""
        seen: set[str] = set()
        queue = list(changed)
        while queue:
            name = queue.pop()
            for dependent in self._dependents.get(name, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    queue.append(dependent)
        return seen

    def inputs_of(self, fields: Iterable[str]) -> set[str]:
        """
        Values the given calculated fields read from the row.

        The union of their formula variables minus the fields themselves: a
        calculated dependency inside `fields` is produced earlier in the same
        pass, while one outside must come from the row's stored value.
        """
        target = set(fields)
        needed: set[str] = set()
        for field in target:
            entry = self._formulas_by_name.get(field)
            if entry is not None:
                needed |= _collect_variables(entry[1])
        return needed - target

    def materialize(self, values: dict[str, object]) -> None:
        """
        Пересчитывает расчётные поля в `values` на месте.

        Расчётные поля без формулы удаляются (остаются незаполненными).
        Числовые результаты записываются как Decimal, даты — как date.
        """
        for field in self._calculated_fields:
            values.pop(field, None)
        for field, cell_type, expression, decimal_places in self._formulas:
            self._materialize_field(values, field, cell_type, expression, decimal_places)

    def materialize_fields(self, values: dict[str, object], fields: set[str]) -> None:
        """Recompute only the given calculated fields (the dependency closure)."""
        for field in fields:
            values.pop(field, None)
        for field, cell_type, expression, decimal_places in self._formulas:
            if field in fields:
                self._materialize_field(values, field, cell_type, expression, decimal_places)

    def _materialize_field(
        self,
        values: dict[str, object],
        field: str,
        cell_type: str,
        expression: _Expression,
        decimal_places: int | None,
    ) -> None:
        with localcontext(_ARITHMETIC_CONTEXT):
            evaluated = _evaluate(expression, values, self._field_types)
            if isinstance(evaluated, _EvaluatedMissingDate):
                return
            if isinstance(evaluated, _EvaluatedNumber):
                if cell_type != NUMBER_CELL:
                    raise _target_type_mismatch(field)
                values[field] = _round_to_places(evaluated.value, decimal_places)
            elif isinstance(evaluated, _EvaluatedDate):
                if cell_type != DATE_CELL:
                    raise _target_type_mismatch(field)
                values[field] = evaluated.value
            else:
                raise _target_type_mismatch(field)


def _collect_variables(expression: _Expression) -> set[str]:
    if isinstance(expression, _Variable):
        return {expression.name}
    if isinstance(expression, _Negate):
        return _collect_variables(expression.inner)
    if isinstance(expression, _Binary):
        return _collect_variables(expression.left) | _collect_variables(expression.right)
    if isinstance(expression, _Compare):
        return _collect_variables(expression.left) | _collect_variables(expression.right)
    if isinstance(expression, _Call):
        result: set[str] = set()
        for arg in expression.args:
            result |= _collect_variables(arg)
        return result
    return set()


def _topological_order(
    formulas: dict[str, tuple[str, _Expression, int | None]],
) -> list[str]:
    states: dict[str, str] = {}
    order: list[str] = []

    def visit(field: str, stack: list[str]) -> None:
        state = states.get(field)
        if state == "complete":
            return
        if state == "visiting":
            start = next((i for i, item in enumerate(stack) if item == field), 0)
            cycle = [*stack[start:], field]
            raise _cycle(" -> ".join(cycle))
        states[field] = "visiting"
        stack.append(field)
        dependencies = sorted(_collect_variables(formulas[field][1]))
        for dependency in dependencies:
            if dependency in formulas:
                visit(dependency, stack)
        stack.pop()
        states[field] = "complete"
        order.append(field)

    for field in sorted(formulas):
        visit(field, [])
    return order


def _evaluate(
    expression: _Expression,
    values: dict[str, object],
    field_types: dict[str, str],
) -> _Evaluated:
    if isinstance(expression, _Number):
        return _finite_number(expression.value)
    if isinstance(expression, _Variable):
        return _evaluate_variable(expression.name, values, field_types)
    if isinstance(expression, _Negate):
        evaluated = _evaluate(expression.inner, values, field_types)
        if isinstance(evaluated, _EvaluatedNumber):
            return _finite_number(-evaluated.value)
        if isinstance(evaluated, _EvaluatedMissingDate):
            return _EvaluatedMissingDate()
        raise _invalid_operation(_UNARY_NEGATE_LABEL)
    if isinstance(expression, _Binary):
        left_value = _evaluate(expression.left, values, field_types)
        right_value = _evaluate(expression.right, values, field_types)
        return _evaluate_binary(expression.operator, left_value, right_value)
    if isinstance(expression, _Compare):
        left_value = _evaluate(expression.left, values, field_types)
        right_value = _evaluate(expression.right, values, field_types)
        return _evaluate_compare(left_value, right_value)
    if isinstance(expression, _Call):
        return _evaluate_call(expression, values, field_types)
    raise _invalid_value_type(_UNKNOWN_VALUE_LABEL)


def _evaluate_compare(left: _Evaluated, right: _Evaluated) -> _Evaluated:
    if isinstance(left, _EvaluatedMissingDate) or isinstance(right, _EvaluatedMissingDate):
        return _EvaluatedNumber(Decimal(0))
    if isinstance(left, _EvaluatedNumber) and isinstance(right, _EvaluatedNumber):
        return _EvaluatedNumber(Decimal(1) if left.value == right.value else Decimal(0))
    if isinstance(left, _EvaluatedDate) and isinstance(right, _EvaluatedDate):
        return _EvaluatedNumber(Decimal(1) if left.value == right.value else Decimal(0))
    raise _invalid_operation(_COMPARE_LABEL)


def _call_date(
    expression: _Expression,
    values: dict[str, object],
    field_types: dict[str, str],
) -> date | None:
    """Return the date behind a `YEAR`/`MONTH` argument (None when it is empty)."""
    evaluated = _evaluate(expression, values, field_types)
    if isinstance(evaluated, _EvaluatedDate):
        return evaluated.value
    if isinstance(evaluated, _EvaluatedMissingDate):
        return None
    raise _invalid_operation(_DATE_LABEL)


def _evaluate_call(
    call: _Call,
    values: dict[str, object],
    field_types: dict[str, str],
) -> _Evaluated:
    name = call.name.upper()
    if name == "YEAR":
        if len(call.args) != 1:
            raise _invalid_operation(name)
        parsed = _call_date(call.args[0], values, field_types)
        return _EvaluatedMissingDate() if parsed is None else _EvaluatedNumber(Decimal(parsed.year))
    if name == "MONTH":
        if len(call.args) != 1:
            raise _invalid_operation(name)
        parsed = _call_date(call.args[0], values, field_types)
        return _EvaluatedMissingDate() if parsed is None else _EvaluatedNumber(Decimal(parsed.month))
    if name == "AND":
        if len(call.args) < 2:
            raise _invalid_operation(name)
        for arg in call.args:
            evaluated = _evaluate(arg, values, field_types)
            if not isinstance(evaluated, _EvaluatedNumber) or evaluated.value == 0:
                return _EvaluatedNumber(Decimal(0))
        return _EvaluatedNumber(Decimal(1))
    if name == "IF":
        if len(call.args) != 3:
            raise _invalid_operation(name)
        condition = _evaluate(call.args[0], values, field_types)
        truthy = isinstance(condition, _EvaluatedNumber) and condition.value != 0
        return _evaluate(call.args[1] if truthy else call.args[2], values, field_types)
    raise _invalid_operation(call.name)


def _evaluate_variable(
    field: str,
    values: dict[str, object],
    field_types: dict[str, str],
) -> _Evaluated:
    field_type = field_types.get(field)
    if field_type is None:
        raise _unknown_dependency(field, field)
    value = values.get(field)
    if field_type == NUMBER_CELL:
        if value is None:
            return _EvaluatedNumber(Decimal(0))
        if isinstance(value, bool):
            raise _invalid_value_type(field)
        if isinstance(value, Decimal):
            return _finite_number(value)
        if isinstance(value, int):
            return _finite_number(Decimal(value))
        # A float input is normalized through its shortest round-tripping text,
        # never used as an arithmetic operand.
        if isinstance(value, float):
            if not math.isfinite(value):
                raise _overflow()
            return _finite_number(Decimal(str(value)))
        raise _invalid_value_type(field)
    if field_type == DATE_CELL:
        if value is None:
            return _EvaluatedMissingDate()
        if isinstance(value, date) and not isinstance(value, datetime):
            return _EvaluatedDate(value)
        if isinstance(value, str):
            parsed = _parse_date(value)
            if parsed is None:
                raise _invalid_date(field)
            return _EvaluatedDate(parsed)
        raise _invalid_value_type(field)
    raise _unsupported_dependency(field, field)


def _evaluate_binary(operator: str, left: _Evaluated, right: _Evaluated) -> _Evaluated:
    if isinstance(left, _EvaluatedMissingDate) or isinstance(right, _EvaluatedMissingDate):
        return _EvaluatedMissingDate()
    left_number = left.value if isinstance(left, _EvaluatedNumber) else None
    right_number = right.value if isinstance(right, _EvaluatedNumber) else None
    left_date = left.value if isinstance(left, _EvaluatedDate) else None
    right_date = right.value if isinstance(right, _EvaluatedDate) else None

    try:
        if operator == "+":
            if left_number is not None and right_number is not None:
                return _finite_number(left_number + right_number)
            if left_date is not None and right_number is not None:
                return _shift_date(left_date, right_number, add=True)
            if left_number is not None and right_date is not None:
                return _shift_date(right_date, left_number, add=True)
            raise _invalid_operation(operator)
        if operator == "-":
            if left_number is not None and right_number is not None:
                return _finite_number(left_number - right_number)
            if left_date is not None and right_date is not None:
                return _finite_number(Decimal((left_date - right_date).days))
            if left_date is not None and right_number is not None:
                return _shift_date(left_date, right_number, add=False)
            raise _invalid_operation(operator)
        if operator == "*":
            if left_number is not None and right_number is not None:
                return _finite_number(left_number * right_number)
            raise _invalid_operation(operator)
        if operator == "/":
            if left_number is not None and right_number is not None:
                if right_number == 0:
                    raise _division_by_zero()
                return _finite_number(left_number / right_number)
            raise _invalid_operation(operator)
        raise _invalid_operation(operator)
    except DivisionByZero as error:
        raise _division_by_zero() from error
    except (Overflow, InvalidOperation) as error:
        raise _overflow() from error


def _shift_date(value: date, days: Decimal, add: bool) -> _Evaluated:
    if not days.is_finite() or days != days.to_integral_value():
        raise _fractional_days()
    count = int(days)
    if not -_MAX_DATE_SHIFT <= count <= _MAX_DATE_SHIFT:
        raise _overflow()
    delta = timedelta(days=count)
    try:
        shifted = value + delta if add else value - delta
    except OverflowError as error:
        raise _overflow() from error
    return _EvaluatedDate(shifted)


def _finite_number(number: Decimal) -> _Evaluated:
    if not number.is_finite():
        raise _overflow()
    if number == 0:
        return _EvaluatedNumber(Decimal(0))
    return _EvaluatedNumber(number)


def _round_to_places(number: Decimal, places: int | None) -> Decimal:
    if not number.is_finite():
        raise _overflow()
    if places is None:
        return Decimal(0) if number == 0 else number
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, Overflow) as error:
        raise _overflow() from error
    return Decimal(0) if rounded == 0 else rounded


def _parse_date(source: str) -> date | None:
    if len(source) != 10:
        return None
    try:
        return date.fromisoformat(source)
    except ValueError:
        return None


class _Parser:
    """Рекурсивный нисходящий парсер выражений формул (эталон: formula.rs)."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._position = 0

    def parse(self) -> _Expression:
        if not self._source.isascii():
            raise _ParserError(_PARSE_ASCII_ONLY)
        expression = self._parse_comparison()
        self._skip_whitespace()
        if self._position != len(self._source):
            raise _ParserError(_PARSE_UNEXPECTED_CHAR.format(position=self._position))
        return expression

    def _parse_comparison(self) -> _Expression:
        expression = self._parse_expression()
        if self._consume("="):
            return _Compare(expression, self._parse_expression())
        return expression

    def _parse_expression(self) -> _Expression:
        expression = self._parse_term()
        while True:
            if self._consume("+"):
                operator = "+"
            elif self._consume("-"):
                operator = "-"
            else:
                return expression
            expression = _Binary(operator, expression, self._parse_term())

    def _parse_term(self) -> _Expression:
        expression = self._parse_factor()
        while True:
            if self._consume("*"):
                operator = "*"
            elif self._consume("/"):
                operator = "/"
            else:
                return expression
            expression = _Binary(operator, expression, self._parse_factor())

    def _parse_factor(self) -> _Expression:
        if self._consume("-"):
            return _Negate(self._parse_factor())
        if self._consume("("):
            expression = self._parse_expression()
            if not self._consume(")"):
                raise _ParserError(_PARSE_UNCLOSED_PAREN)
            return expression
        return self._parse_value()

    def _parse_value(self) -> _Expression:
        self._skip_whitespace()
        first = self._peek()
        if first is None:
            raise _ParserError(_PARSE_EXPECTED_VALUE)
        if first.isascii() and (first.isalpha() or first == "_"):
            start = self._position
            self._position += 1
            while True:
                peeked = self._peek()
                if peeked is None or not peeked.isascii() or not (peeked.isalnum() or peeked == "_"):
                    break
                self._position += 1
            name = self._source[start : self._position]
            self._skip_whitespace()
            if self._peek() == "(":
                # A function call: `IF(cond, a, b)`, `AND(...)`, `YEAR(f)`, `MONTH(f)`.
                self._position += 1
                args: list[_Expression] = []
                self._skip_whitespace()
                if self._peek() != ")":
                    args.append(self._parse_comparison())
                    while self._consume(","):
                        args.append(self._parse_comparison())
                if not self._consume(")"):
                    raise _ParserError(_PARSE_UNCLOSED_PAREN)
                return _Call(name, tuple(args))
            return _Variable(name)
        if first.isascii() and (first.isdigit() or first == "."):
            return self._parse_number()
        raise _ParserError(_PARSE_EXPECTED_VALUE)

    def _parse_number(self) -> _Expression:
        start = self._position
        integer_digits = 0
        while True:
            peeked = self._peek()
            if peeked is None or not peeked.isascii() or not peeked.isdigit():
                break
            integer_digits += 1
            self._position += 1
        fraction_digits = 0
        if self._peek() == ".":
            self._position += 1
            while True:
                peeked = self._peek()
                if peeked is None or not peeked.isascii() or not peeked.isdigit():
                    break
                fraction_digits += 1
                self._position += 1
        if integer_digits == 0 and fraction_digits == 0:
            raise _ParserError(_PARSE_INVALID_NUMBER)
        if self._peek() in ("e", "E"):
            self._position += 1
            if self._peek() in ("+", "-"):
                self._position += 1
            exponent_start = self._position
            while True:
                peeked = self._peek()
                if peeked is None or not peeked.isascii() or not peeked.isdigit():
                    break
                self._position += 1
            if exponent_start == self._position:
                raise _ParserError(_PARSE_EXPONENT_DIGITS)
        literal = self._source[start : self._position]
        try:
            number = Decimal(literal)
            exponent = number.adjusted()
        except InvalidOperation as error:
            raise _ParserError(_PARSE_INVALID_NUMBER) from error
        if not number.is_finite() or exponent > _MAX_LITERAL_EXPONENT:
            raise _ParserError(_PARSE_FINITE_LITERAL)
        return _Number(number)

    def _consume(self, expected: str) -> bool:
        self._skip_whitespace()
        if self._peek() == expected:
            self._position += 1
            return True
        return False

    def _skip_whitespace(self) -> None:
        while True:
            peeked = self._peek()
            if peeked is None or not peeked.isspace():
                return
            self._position += 1

    def _peek(self) -> str | None:
        if self._position < len(self._source):
            return self._source[self._position]
        return None
