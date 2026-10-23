"""Тесты порта renovation_id (эталон: renovation_id.rs, семантика NULL вместо <УГАДАЙТЕ>)."""

import pytest

from src.renovation.renovation_id import (
    NO_DV_FIELD,
    PROJECT_CODE_FIELD,
    RenovationIdError,
    RenovationIdInput,
    calculate_renovation_id,
    is_improvement_project,
)


def base_input(**overrides: str | None) -> RenovationIdInput:
    values = {
        "start_date": "2027-01-01",
        "asset_repair_code": "BSKR",
        "direction_code": "1.1.1.",
        "technology_position": "ID39TLA",
        "work_type_label": "ТР",
    }
    values.update(overrides)
    return RenovationIdInput(**values)


def test_preserves_an_existing_identifier_byte_for_byte() -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            existing_id="  ASSIGNED_CODE  ",
            start_date="not-a-date",
        )
    )
    assert result.value == "  ASSIGNED_CODE  "
    assert result.missing_components == ()


def test_builds_the_documented_defect_statement_identifier() -> None:
    result = calculate_renovation_id(base_input(defect_number="1382467"))
    # The direction component ends on the last digit (no trailing dot) and the
    # optional typical-DV numbering is absent — the spec's example form.
    assert result.value == "2027_BSKR_1.1.1._1382467"
    assert result.missing_components == ()


def test_appends_a_two_digit_standard_statement_suffix_only_when_given() -> None:
    result = calculate_renovation_id(base_input(defect_number="1382467", typical_defect_suffix="02"))
    assert result.value == "2027_BSKR_1.1.1._1382467_02"


def test_without_defect_builds_the_composite_object_code() -> None:
    # Без ДВ объектная часть собирается по п. 5.2: техпозиция*вид работ*месяц.
    result = calculate_renovation_id(base_input())
    assert result.value == "2027_BSKR_1.1.1._ID39TLA*ТР*Январь"
    assert result.missing_components == ()


def test_without_defect_marks_missing_composite_parts() -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            start_date="2027-01-01",
            asset_repair_code="BSKR",
            direction_code="1.1.1.",
        )
    )
    assert result.value == "2027_BSKR_1.1.1._<NULL>*<NULL>*Январь"
    assert result.missing_components == ("technology_position", "work_type")


def test_without_defect_and_without_start_date_nulls_the_month() -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            asset_repair_code="BSKR",
            direction_code="1.1.1.",
            technology_position="ID39TLA",
            work_type_label="ТР",
        )
    )
    assert result.value == "<NULL>_BSKR_1.1.1._ID39TLA*ТР*<NULL>"
    assert result.missing_components == ("start_date",)


def test_abbreviates_known_work_types_and_keeps_unknown_labels() -> None:
    result = calculate_renovation_id(base_input(work_type_label="Испытания"))
    assert result.value.endswith("*ИСП*Январь")

    result = calculate_renovation_id(base_input(work_type_label="Своя формулировка"))
    assert result.value.endswith("*Своя формулировка*Январь")


def test_improvement_project_uses_project_code_before_defect_statement() -> None:
    result = calculate_renovation_id(
        base_input(
            bpof_section="Проекты улучшения",
            project_code="10.2.44.7",
            defect_number="1382467",
            typical_defect_suffix="02",
        )
    )
    assert result.value == "2027_BSKR_1.1.1._10.2.44.7"


def test_improvement_category_accepts_value_id_or_display_label() -> None:
    assert is_improvement_project(None, "work_category_007")
    assert is_improvement_project(None, "Проект улучшения")
    assert is_improvement_project("ПРОЕКТЫ УЛУЧШЕНИЯ", None)
    assert not is_improvement_project("Ремонтный бюджет", "work_category_001")


def test_improvement_project_uses_null_placeholder_when_project_code_is_missing() -> None:
    result = calculate_renovation_id(base_input(work_category="work_category_007", defect_number="1382467"))
    assert result.value == "2027_BSKR_1.1.1._<NULL>"
    assert result.missing_components == ("project_code",)


def test_normalizes_lowercase_repair_code_and_adds_the_lo_t_trailing_dot() -> None:
    """A code typed without the dot in the end gets it added, never rejected."""
    result = calculate_renovation_id(
        base_input(
            asset_repair_code="bskr",
            direction_code="1.1.1",
            defect_number="1382467",
        )
    )
    assert result.value == "2027_BSKR_1.1.1._1382467"

    # With the dot already there the result is identical.
    dotted = calculate_renovation_id(
        base_input(
            asset_repair_code="bskr",
            direction_code="1.1.1.",
            defect_number="1382467",
        )
    )
    assert dotted.value == result.value


def test_normalize_direction_code_always_returns_the_dotted_form() -> None:
    from src.renovation.renovation_id import normalize_direction_code

    assert normalize_direction_code("1.1.1") == "1.1.1."
    assert normalize_direction_code("1.1.1.") == "1.1.1."
    assert normalize_direction_code("  10.2.44 ") == "10.2.44."
    # Not a direction code at all — returned trimmed, the validator reports it.
    assert normalize_direction_code("1.1") == "1.1"
    assert normalize_direction_code("abc") == "abc"
    assert normalize_direction_code("") is None


def test_accepts_two_to_thirty_two_letter_repair_codes() -> None:
    from src.renovation.renovation_id import _normalize_asset_repair_code

    assert _normalize_asset_repair_code("do") == "DO"
    assert _normalize_asset_repair_code("abcdefghijklmnopqrstuvwxyzabcdef") == "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEF"


def test_maps_every_month_to_its_russian_name() -> None:
    from src.renovation.renovation_id import _russian_month_name

    months = [
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
    ]
    for index, expected in enumerate(months):
        assert _russian_month_name(index + 1) == expected
    assert _russian_month_name(0) is None
    assert _russian_month_name(13) is None


def test_rejects_nonexistent_or_noncanonical_dates() -> None:
    for value in ["2027-02-29", "2027-2-01", "27-02-01", "2027/02/01"]:
        with pytest.raises(RenovationIdError, match="некорректная дата начала работ"):
            calculate_renovation_id(base_input(start_date=value))


def test_rejects_invalid_repair_and_direction_codes() -> None:
    for repair_code in ["B", "BSK12", "БСКР", "abcdefghijklmnopqrstuvwxyzabcdefg"]:
        with pytest.raises(RenovationIdError, match="некорректный код ДО"):
            calculate_renovation_id(base_input(asset_repair_code=repair_code))

    for direction_code in ["1.1", "1..1", "1.1.A", "1.1.1._"]:
        with pytest.raises(RenovationIdError, match="некорректный код направления"):
            calculate_renovation_id(base_input(direction_code=direction_code))


def test_rejects_invalid_defect_numbers_and_suffixes() -> None:
    for defect_number in ["123456", "12345678", "12345A7"]:
        with pytest.raises(RenovationIdError, match="некорректный номер ДВ"):
            calculate_renovation_id(base_input(defect_number=defect_number))

    for suffix in ["1", "001", "A1"]:
        with pytest.raises(RenovationIdError, match="некорректная дополнительная нумерация"):
            calculate_renovation_id(base_input(defect_number="1382467", typical_defect_suffix=suffix))


def test_rejects_a_standard_statement_suffix_without_a_defect_number() -> None:
    with pytest.raises(RenovationIdError, match="без семизначного номера ДВ"):
        calculate_renovation_id(base_input(typical_defect_suffix="01"))


def test_allows_reserved_separators_in_composite_but_rejects_control_characters() -> None:
    # `_` и `*` допустимы в свободных частях составного кода и не парсятся назад.
    result = calculate_renovation_id(base_input(technology_position="ЛабСтолВПУ_210"))
    assert "_ЛабСтолВПУ_210*ТР*Январь" in result.value

    with pytest.raises(RenovationIdError) as error:
        calculate_renovation_id(base_input(technology_position="стро\nка"))
    assert f"{NO_DV_FIELD} содержит управляющий символ или перевод строки" in str(error.value)

    # проект улучшения продолжает проверяться собственным сообщением
    with pytest.raises(RenovationIdError) as error:
        calculate_renovation_id(base_input(bpof_section="Проекты улучшения", project_code="10.2\n44.7"))
    assert f"{PROJECT_CODE_FIELD} содержит управляющий символ или перевод строки" in str(error.value)


def test_rejects_empty_project_code_segments() -> None:
    for project_code in [".10.2", "10.2.", "10..2", "10. .2"]:
        with pytest.raises(RenovationIdError, match="некорректный код проекта"):
            calculate_renovation_id(base_input(work_category="Проект улучшения", project_code=project_code))


def test_every_missing_required_component_is_listed_deterministically() -> None:
    result = calculate_renovation_id(RenovationIdInput())
    assert result.value == "<NULL>_<NULL>_<NULL>_<NULL>*<NULL>*<NULL>"
    assert result.missing_components == (
        "start_date",
        "asset_repair_code",
        "direction_code",
        "technology_position",
        "work_type",
    )


def test_missing_start_date_substitutes_null_for_year() -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            asset_repair_code="BSKR",
            direction_code="1.1.1.",
            technology_position="ID39TLA",
            work_type_label="ТР",
        )
    )
    assert result.value == "<NULL>_BSKR_1.1.1._ID39TLA*ТР*<NULL>"
    assert result.missing_components == ("start_date",)


def test_missing_repair_code_substitutes_null_in_place() -> None:
    result = calculate_renovation_id(base_input(asset_repair_code=None, defect_number="1382467"))
    assert result.value == "2027_<NULL>_1.1.1._1382467"
    assert result.missing_components == ("asset_repair_code",)


def test_missing_direction_code_substitutes_null_in_place() -> None:
    result = calculate_renovation_id(base_input(direction_code=None, defect_number="1382467"))
    assert result.value == "2027_BSKR_<NULL>_1382467"
    assert result.missing_components == ("direction_code",)


def test_empty_components_are_treated_as_missing() -> None:
    result = calculate_renovation_id(
        RenovationIdInput(
            start_date="2027-01-01",
            asset_repair_code=" BSKR ",
            direction_code="1.1.1.",
            defect_number="1382467",
            technology_position="  ",
            work_type_label="",
        )
    )
    assert result.value == "2027_BSKR_1.1.1._1382467"
