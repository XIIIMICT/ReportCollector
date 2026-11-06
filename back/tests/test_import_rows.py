"""Parsing a technical-directorate plan sheet into row values.

Covers `parse_plan_rows` — the CPU-bound half of the Excel load that runs in a
worker thread. Only the workbook READ (`_read_rows`) is patched: header
location, column mapping, the numbering row, code/asset extraction, reference
normalization and type coercion all run for real, which is what the loader
writes into the plan later. It had no tests at all before (14% coverage), and it
is the step where a mistake silently writes the wrong values into the plan.
"""

from pathlib import Path

import pytest

from src.renovation.import_plan import _ASSET_HEADER, _CODE_HEADER
from src.renovation.import_rows import parse_plan_rows

CODE_COL = 2
ASSET_COL = 3


def _sheet(*data_rows: list[object], numbering: bool = True) -> list[list[object]]:
    """Build a sheet whose first row is the template header the parser recognises."""
    header: list[object] = ["", "", _CODE_HEADER, _ASSET_HEADER, "Цех", "Наименование ЕО", "Примечание"]
    rows: list[list[object]] = [header]
    if numbering:
        rows.append(["", "", 1, 2, 3, 4, 5])
    rows.extend(data_rows)
    return rows


def _row(code: object, asset: object, workshop: object = None, equipment: object = None) -> list[object]:
    return ["", "", code, asset, workshop, equipment, None]


@pytest.fixture
def sheet(monkeypatch):
    """Replace only the workbook read; hand the parser a plain row matrix."""
    state: dict[str, list[list[object]]] = {"rows": []}

    def fake_read_rows(_source: object, _suffix: str | None = None) -> list[list[object]]:
        return state["rows"]

    monkeypatch.setattr("src.renovation.import_rows._read_rows", fake_read_rows)
    return state


def test_a_data_row_becomes_a_code_asset_and_values(sheet) -> None:
    sheet["rows"] = _sheet(_row("Р-001", " snhz ", workshop="Цех 1"))

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert len(parsed) == 1
    row = parsed[0]
    assert row.code == "Р-001"
    # The asset code is upper-cased: template codes are case-insensitive.
    assert row.asset_code == "SNHZ"
    assert row.values["col_005"] == "Цех 1"


def test_the_numbering_helper_row_is_not_a_data_row(sheet) -> None:
    """Row 2 of the template counts columns; it has no work code and must go."""
    sheet["rows"] = _sheet(_row("Р-001", "SNHZ"))

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert [row.code for row in parsed] == ["Р-001"]


def test_rows_without_a_work_code_are_skipped(sheet) -> None:
    sheet["rows"] = _sheet(
        _row(None, "SNHZ", workshop="без кода"),
        _row("", "SNHZ", workshop="пустой код"),
        _row("  ", "SNHZ", workshop="пробелы"),
        _row("Р-002", "SNHZ"),
    )

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert [row.code for row in parsed] == ["Р-002"]


def test_a_fully_blank_row_is_skipped(sheet) -> None:
    sheet["rows"] = _sheet([None, None, None, None, None, None, None], _row("Р-003", "SNHZ"))

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert [row.code for row in parsed] == ["Р-003"]


def test_a_missing_asset_leaves_an_empty_code(sheet) -> None:
    """The asset is optional here — the writer rejects an unknown one later."""
    sheet["rows"] = _sheet(_row("Р-004", None))

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert parsed[0].asset_code == ""


def test_a_numeric_reference_is_normalized_to_its_catalog_label(sheet) -> None:
    """Excel hands option "3" over as 3.0; the catalog label is "3".

    The analyzer and the loader must agree on what a known option looks like,
    so the normalization happens here, once.
    """
    sheet["rows"] = _sheet(_row("Р-005", "SNHZ"))
    sheet["rows"][0].append("Категория работ")
    sheet["rows"][-1] = [*sheet["rows"][-1], 3.0]

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    values = parsed[0].values
    assert "3" in values.values(), values


def test_blank_cells_are_left_out_instead_of_stored_empty(sheet) -> None:
    """A blank cell means "no value" — never an empty string in the row."""
    sheet["rows"] = _sheet(_row("Р-006", "SNHZ", workshop=None, equipment="ЕО-1"))

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    values = parsed[0].values
    assert "col_005" not in values
    assert values["col_009"] == "ЕО-1"


def test_a_sheet_without_the_code_column_is_refused(sheet) -> None:
    """The code header is present (so the sheet IS a plan), the asset column is not."""
    sheet["rows"] = [["", _CODE_HEADER, "Что-то другое"], ["", "Р-001", "значение"]]

    with pytest.raises(ValueError, match="work-code column"):
        parse_plan_rows(Path("ignored.xlsb"))


def test_a_sheet_without_a_recognisable_header_is_refused(sheet) -> None:
    sheet["rows"] = [["случайный", "текст"], ["и", "ещё"]]

    with pytest.raises(ValueError, match="no plan sheet header"):
        parse_plan_rows(Path("ignored.xlsb"))


def test_an_empty_sheet_yields_no_rows(sheet) -> None:
    sheet["rows"] = []

    with pytest.raises(ValueError, match="no plan sheet header"):
        parse_plan_rows(Path("ignored.xlsb"))


def test_a_header_on_the_second_level_is_still_found(sheet) -> None:
    """Some files carry a merged band above the header row."""
    sheet["rows"] = [
        ["", "", "Сводная таблица"],
        _sheet()[0],
        _row("Р-007", "SNHZ"),
    ]

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert [row.code for row in parsed] == ["Р-007"]


def test_short_rows_do_not_crash_the_parser(sheet) -> None:
    """A trailing row cut before the last column is still a row."""
    sheet["rows"] = _sheet(["", "", "Р-008"])

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert parsed[0].code == "Р-008"
    assert parsed[0].asset_code == ""


def test_calculated_fields_are_never_parsed_from_the_file(sheet) -> None:
    """Derived columns are rebuilt on write; a stale value in the file is ignored."""
    from src.renovation.form_schema import field_metas

    calculated = next(meta for meta in field_metas() if meta.is_calculated)
    sheet["rows"] = _sheet(_row("Р-009", "SNHZ"))
    sheet["rows"][0].append(calculated.label)
    sheet["rows"][-1] = [*sheet["rows"][-1], "999"]

    parsed = parse_plan_rows(Path("ignored.xlsb"))

    assert calculated.db_name not in parsed[0].values


def test_an_ooxml_workbook_is_read_by_the_real_reader(tmp_path: Path) -> None:
    """An `.xlsx` drop goes through openpyxl, not the `.xlsb` reader."""
    from openpyxl import Workbook

    book = Workbook()
    worksheet = book.active
    worksheet.title = "РБ_2027_БСЗ"
    for row in _sheet(_row("Р-010", "SNHZ", workshop="Цех 2")):
        worksheet.append(row)
    path = tmp_path / "plan.xlsx"
    book.save(path)

    parsed = parse_plan_rows(path)

    assert [row.code for row in parsed] == ["Р-010"]
    assert parsed[0].asset_code == "SNHZ"
    assert parsed[0].values["col_005"] == "Цех 2"
