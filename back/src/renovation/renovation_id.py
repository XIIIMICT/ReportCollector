"""
Порт легаси-генератора идентификатора ремонта (work_or_repair_code).

Эталон: ReportCollector-Renovation/src-tauri/src/renovation_id.rs.

Отличия от эталона (решено 2026-08-18, docs/RENOVATION_MIGRATION_CONTEXT.md):
плейсхолдер `<УГАДАЙТЕ>` не используется; отсутствующий обязательный
компонент подставляется в составной идентификатор как литерал `<NULL>`
(не в недостающее поле, а в поле work_or_repair_code). Список отсутствующих
компонентов возвращается в `missing_components` для UI.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date

MISSING_COMPONENT = "<NULL>"

# «Направление деятельности» («Лот») — the loT code always ends with a dot.
DIRECTION_CODE_FIELD = "lotting_activity_direction_code"
# «Дефектная ведомость/ВОР №» — a seven-digit number whose leading zero matters.
DEFECT_NUMBER_FIELD = "defect_list_or_bill_of_quantities_number"

# Whether the renovation-ID generator is ON.
#
# It is OFF: work_or_repair_code is a plain typed value now (imported from the template
# as-is, edited by hand), because the agreement processes and the state of the
# data made the derived value impractical. The whole generator below — the
# component validation, `calculate_renovation_id`, the legacy formula metadata
# and the import's correctness check — stays in the codebase untouched; flipping
# this flag back to True restores the derived behaviour.
RENOVATION_ID_FORMULA_ENABLED = False

IMPROVEMENT_SECTION_LABEL = "проекты улучшения"
IMPROVEMENT_CATEGORY_LABEL = "проект улучшения"
IMPROVEMENT_CATEGORY_VALUE_ID = "work_category_007"

PROJECT_CODE_FIELD = "код проекта"
TECHNOLOGY_POSITION_FIELD = "технологическая позиция (process_position)"
WORK_TYPE_FIELD = "отображаемое значение вида работ (work_type_id)"
# No-DV object code: «Технологическая позиция*Вид работ*Месяц начала» (spec 5.2).
NO_DV_FIELD = "технологическая позиция*вид работ*месяц (без ДВ)"

_MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

_WORK_TYPE_ABBREVIATIONS = {
    "то": "ТО",
    "тр": "ТР",
    "ср": "СР",
    "кр": "КР",
    "чистка": "ЧИСТ",
    "испытания": "ИСП",
    "поверка": "ПОВ",
    "калибровка": "КЛБ",
    "измерения": "ИЗМ",
    "корректирующие работы": "КОРР",
    "работы по фактическому тех.состоянию": "ФТС",
    "вспомогательные работы (леса, изоляция и пр)": "ВР",
    "оснащенность ремонтного персонала": "ОРП",
    "тоир непроизводственных объектов предприятия": "ТОиР НОП",
    "расходы без влияния на механическую готовность": "РБВМГ",
    "экспертиза": "ЭПБ",
    "тех. освидетельствование": "ТехОСВ",
    "диагностические обследования": "ДО",
    "инспекции": "ИНСП",
}


class RenovationIdError(ValueError):
    """Ошибка валидации компонентов идентификатора ремонта."""


def _invalid_start_date(value: str) -> RenovationIdError:
    return RenovationIdError(
        f"некорректная дата начала работ `{value}`: ожидается существующая дата в формате YYYY-MM-DD"
    )


def _invalid_asset_repair_code(value: str) -> RenovationIdError:
    return RenovationIdError(f"некорректный код ДО `{value}`: ожидаются от 2 до 32 латинских букв")


def _invalid_direction_code(value: str) -> RenovationIdError:
    return RenovationIdError(
        f"некорректный код направления деятельности `{value}`: ожидаются три цифровые группы, например 1.1.1 (точка в конце дописывается сама)"
    )


def _invalid_defect_number(value: str) -> RenovationIdError:
    return RenovationIdError(f"некорректный номер ДВ `{value}`: ожидаются ровно семь цифр")


def _invalid_typical_defect_suffix(value: str) -> RenovationIdError:
    return RenovationIdError(f"некорректная дополнительная нумерация типовой ДВ `{value}`: ожидаются ровно две цифры")


def _suffix_without_defect_number() -> RenovationIdError:
    return RenovationIdError(
        "дополнительная нумерация типовой ДВ указана без семизначного номера ДВ (defect_list_or_bill_of_quantities_number)"
    )


def _invalid_project_code(value: str) -> RenovationIdError:
    return RenovationIdError(f"некорректный код проекта `{value}`: точка должна разделять непустые части кода")


def _control_character(field: str) -> RenovationIdError:
    return RenovationIdError(f"{field} содержит управляющий символ или перевод строки")


@dataclass(frozen=True)
class RenovationIdInput:
    """
    Строковые входы, необходимые для материализации work_or_repair_code.

    Вызывающий код обязан разрешить reference-поле work_type_id в отображаемую
    метку до построения этого значения. `work_category` может содержать
    либо сохранённый `value_id`, либо его отображаемую метку.
    """

    # Текущий work_or_repair_code. Каждое непустое значение неизменяемо и возвращается байт-в-байт.
    existing_id: str | None = None
    # work_start_date, строго в форме YYYY-MM-DD при наличии.
    start_date: str | None = None
    # Код из записи актива (от двух до тридцати двух букв), например BSKR.
    asset_repair_code: str | None = None
    # lotting_activity_direction_code, например 1.1.1.
    direction_code: str | None = None
    # Отображаемое значение bpof_section.
    bpof_section: str | None = None
    # work_category_id: идентификатор reference-значения или отображаемая метка.
    work_category: str | None = None
    # Код проекта (plan_adjustment_type), используемый для проектов улучшения.
    project_code: str | None = None
    # defect_list_or_bill_of_quantities_number, представленный текстом, чтобы ведущие нули не терялись.
    defect_number: str | None = None
    # Необязательный двухзначный номер повторной типовой ДВ.
    typical_defect_suffix: str | None = None
    # process_position.
    technology_position: str | None = None
    # Отображаемая метка, разрешённая из work_type_id.
    work_type_label: str | None = None


@dataclass(frozen=True)
class RenovationIdResult:
    """
    Результат расчёта идентификатора ремонта.

    `value` — всегда строка: отсутствующий обязательный компонент
    подставляется как литерал `MISSING_COMPONENT` на своё место в
    составном идентификаторе (в поле work_or_repair_code, не в недостающее поле).
    `missing_components` перечисляет отсутствующие компоненты в
    детерминированном порядке для подсветки ячеек в UI.
    """

    value: str
    missing_components: tuple[str, ...]


def calculate_renovation_id(input_: RenovationIdInput) -> RenovationIdResult:
    """
    Рассчитывает неизменяемый идентификатор работ/ремонта для work_or_repair_code.

    Уже присвоенный непустой идентификатор всегда побеждает, даже если
    остальные входы отсутствуют или невалидны. Формат нового идентификатора
    (спецификация, п. 5): `Год_ДО_КодНаправления_КодОбъекта[_ДопНумерация]`.
    Приоритет кода объекта: проект улучшения (plan_adjustment_type), семизначный номер ДВ
    (defect_list_or_bill_of_quantities_number), затем — при отсутствии ДВ — составной код по п. 5.2
    «Технологическая позиция*Вид работ*Месяц начала» (process_position*work_type_id*work_start_date).
    Дополнительная нумерация типовой ДВ добавляется только когда задана.
    Отсутствующий обязательный компонент подставляется как `<NULL>`.
    """
    if input_.existing_id and input_.existing_id.strip():
        return RenovationIdResult(value=input_.existing_id, missing_components=())

    start_date = _parse_start_date(input_.start_date)
    repair_code = _normalize_asset_repair_code(input_.asset_repair_code)
    direction_code = _normalize_direction_code(input_.direction_code)

    defect_number = _optional_trimmed(input_.defect_number)
    typical_suffix = _optional_trimmed(input_.typical_defect_suffix)
    improvement_project = is_improvement_project(input_.bpof_section, input_.work_category)

    missing: list[str] = []
    if start_date is None:
        missing.append("start_date")
    if repair_code is None:
        missing.append("asset_repair_code")
    if direction_code is None:
        missing.append("direction_code")

    year = f"{start_date.year:04d}" if start_date is not None else MISSING_COMPONENT
    suffix = ""

    if improvement_project:
        project_code = _optional_trimmed(input_.project_code)
        if project_code is not None:
            _validate_project_code(project_code)
        if project_code is None:
            missing.append("project_code")
        object_code = project_code if project_code is not None else MISSING_COMPONENT
    elif defect_number is not None:
        _validate_fixed_ascii_digits(defect_number, 7, _invalid_defect_number(defect_number))
        object_code = defect_number
        if typical_suffix is not None:
            _validate_fixed_ascii_digits(typical_suffix, 2, _invalid_typical_defect_suffix(typical_suffix))
            suffix = f"_{typical_suffix}"
    else:
        if typical_suffix is not None:
            raise _suffix_without_defect_number()
        # Без ДВ объектный код собирается по п. 5.2 спецификации из
        # технологической позиции, вида работ и месяца начала работ.
        technology = _optional_trimmed(input_.technology_position)
        work_type = _optional_trimmed(input_.work_type_label)
        if technology is None:
            missing.append("technology_position")
        if work_type is None:
            missing.append("work_type")
        month = _russian_month_name(start_date.month) if start_date is not None else None
        object_code = "*".join(
            (
                technology if technology is not None else MISSING_COMPONENT,
                _abbreviate_work_type(work_type) if work_type is not None else MISSING_COMPONENT,
                month if month is not None else MISSING_COMPONENT,
            )
        )
        _validate_object_component(object_code, NO_DV_FIELD)

    value = (
        f"{year}_{repair_code if repair_code is not None else MISSING_COMPONENT}"
        f"_{direction_code if direction_code is not None else MISSING_COMPONENT}"
        f"_{object_code}{suffix}"
    )
    unique_missing = tuple(dict.fromkeys(missing))
    return RenovationIdResult(value=value, missing_components=unique_missing)


def _abbreviate_work_type(value: str) -> str:
    return _WORK_TYPE_ABBREVIATIONS.get(value.strip().lower(), value)


def is_improvement_project(bpof_section: str | None, work_category: str | None) -> bool:
    """
    Возвращает, идентифицирует ли хотя бы одно поле проект улучшения.

    Принимает и сохранённые value_id из work_category_id, и отображаемые метки.
    """
    if bpof_section is not None and bpof_section.strip().lower() == IMPROVEMENT_SECTION_LABEL:
        return True
    if work_category is not None:
        value = work_category.strip()
        if value.lower() == IMPROVEMENT_CATEGORY_VALUE_ID:
            return True
        if value.lower() == IMPROVEMENT_CATEGORY_LABEL:
            return True
    return False


def _parse_start_date(value: str | None) -> date | None:
    value = _optional_trimmed(value)
    if value is None:
        return None
    if len(value) != 10 or not _is_iso_shape(value):
        raise _invalid_start_date(value)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _invalid_start_date(value) from error
    if not (1 <= parsed.year <= 9999):
        raise _invalid_start_date(value)
    return parsed


def _is_iso_shape(value: str) -> bool:
    for index, char in enumerate(value):
        if index in (4, 7):
            if char != "-":
                return False
        elif not char.isdigit():
            return False
    return True


def _normalize_asset_repair_code(value: str | None) -> str | None:
    value = _optional_trimmed(value)
    if value is None:
        return None
    if not (2 <= len(value) <= 32) or not value.isascii() or not value.isalpha():
        raise _invalid_asset_repair_code(value)
    return value.upper()


def normalize_direction_code(value: str | None) -> str | None:
    """
    Canonical activity-direction code: three digit groups ending with a dot.

    `1.1.1` and `1.1.1.` both become `1.1.1.` — the loT codes carry the dot by
    definition, so it is added instead of rejected. A value that is not a
    direction code at all is returned unchanged (trimmed), so the ordinary
    validation reports it in its own words.
    """
    text = _optional_trimmed(value)
    if text is None:
        return None
    body = text.removesuffix(".")
    parts = body.split(".")
    valid = len(parts) == 3 and all(part and part.isascii() and part.isdigit() for part in parts)
    return f"{body}." if valid else text


def _normalize_direction_code(value: str | None) -> str | None:
    """Validate the direction code and return its canonical dotted form (errors raise)."""
    value = _optional_trimmed(value)
    if value is None:
        return None
    body = value.removesuffix(".")
    parts = body.split(".")
    valid = len(parts) == 3 and all(part and part.isascii() and part.isdigit() for part in parts)
    if not valid:
        raise _invalid_direction_code(value)
    return f"{body}."


def _validate_project_code(value: str) -> None:
    _validate_object_component(value, PROJECT_CODE_FIELD)
    if any(not part.strip() for part in value.split(".")):
        raise _invalid_project_code(value)


def _validate_object_component(value: str, field: str) -> None:
    # Free-text object components (technological position, work type, project
    # code) may legitimately contain `_` or `*` in real data («ЛабСтолВПУ_210»).
    # They only compose a whole string that is never parsed back into parts, so
    # these separators are harmless; control characters are the real corruption
    # risk and stay rejected.
    if any(_is_control_character(char) for char in value):
        raise _control_character(field)


def _is_control_character(char: str) -> bool:
    if char in ("\u2028", "\u2029"):
        return True
    return unicodedata.category(char) == "Cc"


def _validate_fixed_ascii_digits(value: str, expected_length: int, error: RenovationIdError) -> None:
    if len(value) != expected_length or not value.isascii() or not value.isdigit():
        raise error


def _optional_trimmed(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _russian_month_name(month: int) -> str | None:
    if 1 <= month <= 12:
        return _MONTHS[month - 1]
    return None
