"""Validate a technical-directorate XLSB plan without changing the database."""

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile

from pyxlsb import open_workbook

from src.renovation.constants import R_ASSETS
from src.renovation.form_schema import field_metas, normalize_label
from src.renovation.reference_data import REFERENCE_OPTIONS
from src.renovation.renovation_id import (
    DIRECTION_CODE_FIELD,
    RENOVATION_ID_FORMULA_ENABLED,
    RenovationIdError,
    RenovationIdInput,
    RenovationIdResult,
    calculate_renovation_id,
    normalize_direction_code,
)
from src.renovation.schemas import RenovationPlanImportCellIssue, RenovationPlanImportResult

_CODE_HEADER = "Код работы"
_ASSET_HEADER = "Наименование ДО"
# The direction code («Лот») is read for the work-code check only, never stored.
LOT_FIELD = "__lot__"
_EXCEL_EPOCH = date(1899, 12, 30)
# Fallback for a fresh database that has no assets yet: the plan files carry
# the official repair code, the UI shows the legal-entity name.
_TEMPLATE_ASSETS: dict[str, str] = {repair_code.upper(): name for _, repair_code, name in R_ASSETS}
_REPAIR_CODES: dict[str, str] = {code.upper(): repair_code.upper() for code, repair_code, _ in R_ASSETS}

# Template reference: header texts of the columns we load from the current
# «РБ ПОФ» drop. Matching is by name only (see `_header_map`); this tuple pins
# the exact headings so a wording change is caught instead of silently skipped.
TEMPLATE_COLUMNS: tuple[str, ...] = (
    "Наименование производства",
    "Цех",
    "Отделение/установка",
    "Класс оборудования",
    "Вид (категория) оборудования",
    "Наименование ЕО",
    "Название технического места",
    "Технологическая позиция",
    "Инвентарный №",
    "Функциональное направление",
    "Влияние на производство",
    "Группа критичности оборудования",
    "Раздел БПОФ",
    "Категория работ",
    "Вид работ",
    "Отнесение затрат",
    "Способ выполнения работ",
    "Содержание работ",
    (
        "Дефектная ведомость/ВОР № (в случаях когда инв.№ распространяется на отделение "
        "или установку, или блок из нескольких едениц оборудования)"
    ),
    "Дата начала работ",
    "Дата окончания работ",
    "Трудозатраты чел.час",
    "Работа, руб. без ндс",
    "МТР Заказчика, руб. без НДС",
    "МТР Подрядчик, руб. без НДС",
    "Стоимость итого, руб. без НДС",
)

# The template is matched by column NAMES only — never by their position: the
# customer adds/removes columns between drops. Columns that are present but not
# in `_TEMPLATE_FIELDS` (year, lot, numbering, pressure/temperature, repair
# months, fact/forecast/reason) are ignored.


def _norm(value: object) -> str:
    return normalize_label(str(value or "")).casefold()


# Header texts of the template → plan-row field (the frozen reference captured
# from the current «РБ ПОФ» drop). A file with an extra column simply maps it to
# nothing; a missing one is skipped.
_TEMPLATE_FIELDS: dict[str, str] = {
    _norm("Наименование производства"): "col_004",
    _norm("Цех"): "col_005",
    _norm("Отделение/установка"): "col_006",
    _norm("Класс оборудования"): "col_007",
    _norm("Вид (категория) оборудования"): "col_008",
    _norm("Наименование ЕО"): "col_009",
    _norm("Название технического места"): "col_010",
    _norm("Технологическая позиция"): "col_011",
    _norm("Инвентарный №"): "col_012",
    _norm("Функциональное направление"): "col_013",
    _norm("Влияние на производство"): "col_014",
    _norm("Группа критичности оборудования"): "col_015",
    _norm("Раздел БПОФ"): "col_016",
    _norm("Категория работ"): "col_017",
    _norm("Вид работ"): "col_018",
    _norm("Отнесение затрат"): "col_019",
    _norm("Способ выполнения работ"): "col_020",
    _norm("Содержание работ"): "col_022",
    _norm(
        "Дефектная ведомость/ВОР № (в случаях когда инв.№ распространяется на отделение "
        "или установку, или блок из нескольких едениц оборудования)"
    ): "col_023",
    _norm("Дата начала работ"): "col_024",
    _norm("Дата окончания работ"): "col_025",
    _norm("Трудозатраты чел.час"): "col_027",
    _norm("Работа, руб. без ндс"): "col_028",
    _norm("МТР Заказчика, руб. без НДС"): "col_029",
    _norm("МТР Подрядчик, руб. без НДС"): "col_030",
    _norm("Стоимость итого, руб. без НДС"): "col_031",
}


def assets_in_file(source: Path | BinaryIO) -> frozenset[str]:
    """
    Repair codes («Наименование ДО») present in the workbook's plan sheet.

    Used to scope the cache key: a plan change in another asset does not
    invalidate this file's report. Read errors yield an empty set, which the
    caller treats as «unknown» (no caching).
    """
    try:
        rows = _read_rows(source)
    except BadZipFile, KeyError, OSError, ValueError:
        return frozenset()
    header, data_start = _locate_header(rows)
    if header is None:
        return frozenset()
    code_index = next((i for i, value in enumerate(header) if _norm(value) == _norm(_CODE_HEADER)), None)
    if data_start < len(rows) and code_index is not None and _is_numbering_row(rows[data_start], code_index):
        data_start += 1
    asset_index = next((i for i, value in enumerate(header) if _norm(value) == _norm(_ASSET_HEADER)), None)
    if asset_index is None:
        return frozenset()
    codes = {
        str(raw[asset_index]).strip().upper()
        for raw in rows[data_start:]
        if asset_index < len(raw) and raw[asset_index] not in (None, "")
    }
    return frozenset(code for code in codes if code)


def _locate_header(rows: list[list[object]]) -> tuple[list[object] | None, int]:
    """Return the header row and the first data row index (row 1 or row 2)."""
    if not rows:
        return None, 0
    for level in (0, 1):
        if level < len(rows) and any(_norm(value) == _norm(_CODE_HEADER) for value in rows[level]):
            return rows[level], level + 1
    return None, 0


def _column_name(index: int) -> str:
    """Excel letters of a 0-based column index (0 → A, 27 → AB)."""
    result = ""
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


def _is_numbering_row(raw: list[object], code_index: int) -> bool:
    """Detect the «№ п/п» helper row: plain numbers everywhere, no work code."""
    values = [value for value in raw if value not in (None, "")]
    if not values:
        return False
    if code_index < len(raw):
        code = raw[code_index]
        if code not in (None, "") and not isinstance(code, (int, float)):
            return False
    return all(isinstance(value, int | float) and not isinstance(value, bool) for value in values)


def _header_map() -> dict[str, str]:
    """
    Map a normalized template header to its plan-row field.

    Labels come from the form description; only the headings the customer
    words differently carry an explicit alias («Лот» is the direction code).
    """
    mapping: dict[str, str] = {}
    for field in field_metas():
        mapping.setdefault(_norm(field.label), field.db_name)
    mapping[_norm("Отнесение затрат")] = "col_019"
    mapping[_norm("Способ выполнения работ")] = "col_020"
    mapping[_norm("Лот")] = "col_189"
    return mapping


def _reference_values() -> dict[str, set[str]]:
    """Return the form's own reference options plus the plan's known aliases."""
    values: dict[str, set[str]] = {}
    for catalog, label in REFERENCE_OPTIONS:
        values.setdefault(catalog, set()).add(label.casefold())
    for catalog, labels in _CATALOG_ALIASES.items():
        values.setdefault(catalog, set()).update(labels)
    return values


# Source wording that the form stores under a different, canonical label. A
# closed list: anything else absent from the catalog is reported to the user.
_CATALOG_ALIASES: dict[str, frozenset[str]] = {
    "work_category": frozenset({"окр"}),
    "work_type": frozenset({"до"}),
    "execution_method": frozenset({"собственными силами"}),
}


def _read_rows(path: Path | BinaryIO) -> list[list[object]]:
    source = str(path) if isinstance(path, Path) else path
    with open_workbook(source) as workbook:
        sheet = next((name for name in workbook.sheets if name.strip().upper().startswith("РБ")), None)
        if sheet is None:
            msg = "no plan sheet"
            raise ValueError(msg)
        with workbook.get_sheet(sheet) as worksheet:
            return [[cell.v for cell in row] for row in worksheet.rows()]


def inspect_plan_file(
    source: Path | BinaryIO,
    *,
    existing_codes: set[str],
    asset_names_by_code: dict[str, str],
    reference_values: dict[str, set[str]] | None = None,
) -> RenovationPlanImportResult:
    """Run template, type, duplicate, formula and summary checks."""
    if isinstance(source, Path) and source.suffix.casefold() != ".xlsb":
        return RenovationPlanImportResult(status="invalid_format")
    try:
        rows = _read_rows(source)
    except BadZipFile, KeyError, OSError, ValueError:
        return RenovationPlanImportResult(status="invalid_format")
    if not rows:
        return RenovationPlanImportResult(status="invalid_format")
    # Row 1 is the header; some drops prepend an info row, then the header is
    # row 2. A numbering row under the headers (if any) holds numbers only and
    # is skipped by `_is_numbering_row`.
    header_row, data_start = _locate_header(rows)
    if header_row is None:
        if len(rows) < 2:
            return RenovationPlanImportResult(status="invalid_format")
        header_row = rows[1]
        data_start = 2
    mapping = _header_map()
    columns: dict[str, int] = {}
    for index, value in enumerate(header_row):
        normalized = _norm(value)
        if not normalized:
            continue
        # The customer's wording wins over the form label when both exist.
        field = mapping.get(normalized)
        if field is None and normalized.startswith(_norm("Дефектная ведомость/ВОР")):
            field = "col_023"
        if field is not None:
            columns.setdefault(field, index)
    # Only a resolvable set of template columns marks a compatible file: the
    # work code and the asset plus at least the majority of the loaded fields.
    code_index = next((i for i, value in enumerate(header_row) if _norm(value) == _norm(_CODE_HEADER)), None)
    asset_index = next((i for i, value in enumerate(header_row) if _norm(value) == _norm(_ASSET_HEADER)), None)
    if code_index is None or asset_index is None or len(columns) < len(TEMPLATE_COLUMNS) // 2:
        return RenovationPlanImportResult(status="invalid_format")
    if data_start < len(rows) and _is_numbering_row(rows[data_start], code_index):
        data_start += 1

    type_errors: list[RenovationPlanImportCellIssue] = []
    missing: list[RenovationPlanImportCellIssue] = []
    unknown_catalogs: set[str] = set()
    assets: set[str] = set()
    codes: list[str] = []
    valid_rows: list[dict[str, object]] = []
    asset_codes: list[str | None] = []
    row_numbers: list[int] = []
    field_by_name = {field.db_name: field for field in field_metas()}
    for row_number, raw in enumerate(rows[data_start:], start=data_start + 1):
        if not any(value not in (None, "") for value in raw):
            continue
        code = str(raw[code_index]).strip() if code_index < len(raw) and raw[code_index] is not None else ""
        asset_code = (
            str(raw[asset_index]).strip().upper() if asset_index < len(raw) and raw[asset_index] is not None else ""
        )
        if code:
            codes.append(code)
            row_numbers.append(row_number)
        if asset_code in asset_names_by_code:
            assets.add(asset_names_by_code[asset_code])
        elif (default_name := _TEMPLATE_ASSETS.get(asset_code)) is not None:
            assets.add(default_name)
        asset_codes.append(asset_code or None)
        values: dict[str, object] = {}
        for field, index in columns.items():
            if index >= len(raw) or raw[index] in (None, ""):
                continue
            meta = field_by_name.get(field)
            if meta is None or meta.is_calculated:
                continue
            # The «Направление деятельности» («Лот») code always ends with a dot:
            # a correct code typed or stored without it gets it added silently.
            if field == DIRECTION_CODE_FIELD:
                values[field] = normalize_direction_code(str(raw[index]))
                continue
            # Reference fields carry labels in the source; text validation of the
            # numeric id is meaningless, so only the catalog lookup runs. The
            # label is stored trimmed: stray spaces must not create a value the
            # catalogue does not know.
            if meta.catalog:
                trimmed = _trimmed_text(raw[index])
                if trimmed is None:
                    continue
                values[field] = True
                label = trimmed
            else:
                value = _coerce(raw[index], meta.cell_type)
                if value is None:
                    type_errors.append(
                        RenovationPlanImportCellIssue(
                            location=f"{_column_name(index)}, строка {row_number}", value=str(raw[index])
                        )
                    )
                    continue
                values[field] = value
                label = raw[index]
            if meta.catalog:
                # STRICT form catalogue: a value that differs in spelling is
                # "absent", unlike the seed's typo-tolerant import.
                known = reference_values.get(meta.catalog) if reference_values is not None else None
                if known is None:
                    unknown_catalogs.add(meta.catalog)
                elif not _value_in_catalog(known, label):
                    missing.append(
                        RenovationPlanImportCellIssue(
                            location=f"{_column_name(index)}, строка {row_number}", value=str(label)
                        )
                    )
        valid_rows.append(values)
    duplicates = sorted(code for code, count in Counter(codes).items() if count > 1)
    # ARCHIVED: the «correctly calculated code» check. The renovation-ID formula
    # is OFF (`service.RENOVATION_ID_FORMULA_ENABLED`) and col_003 is imported
    # from the template as-is, so comparing it with the generator's output would
    # flag every hand-edited code. The check is kept verbatim below, switched
    # off, to be restored together with the formula. Duplicates are the only
    # blocker left, and they keep blocking.
    invalid_codes: list[str] = []
    if RENOVATION_ID_FORMULA_ENABLED:
        for row_number, code, values, asset_code in zip(row_numbers, codes, valid_rows, asset_codes, strict=False):
            try:
                expected = _calculate_code(values, _REPAIR_CODES.get((asset_code or "").upper(), asset_code))
            except RenovationIdError:
                # An unassemblable row (e.g. an empty «Лот») is an invalid code.
                invalid_codes.append(f"{code} (строка {row_number})")
                continue
            if expected.value != code:
                invalid_codes.append(f"{code} (строка {row_number})")
    status = "has_errors" if duplicates or invalid_codes or type_errors or missing else "ready"
    return RenovationPlanImportResult(
        status=status,
        duplicate_codes=_duplicates_with_rows(codes, row_numbers),
        invalid_calculated_codes=_sorted_without_duplicates(invalid_codes),
        invalid_data_types=type_errors,
        missing_dictionary_values=missing,
        assets=sorted(assets),
        new_rows=sum(code not in existing_codes for code in codes),
        rows_to_update=sum(code in existing_codes for code in codes),
    )


def _duplicates_with_rows(codes: list[str], row_numbers: list[int]) -> list[str]:
    """Repeated work codes with the sheet rows they appear on, e.g. `CODE (15, 27)`."""
    rows_by_code: dict[str, list[int]] = {}
    for code, row_number in zip(codes, row_numbers, strict=False):
        rows_by_code.setdefault(code, []).append(row_number)
    return [
        f"{code} (строки {', '.join(str(number) for number in numbers)})"
        for code, numbers in sorted(rows_by_code.items())
        if len(numbers) > 1
    ]


def _sorted_without_duplicates(values: list[str]) -> list[str]:
    """Sort display entries while keeping one line per distinct value."""
    return sorted(dict.fromkeys(values))


def _value_in_catalog(known: set[str], value: object) -> bool:
    """
    Strict form-catalog lookup (no typo or «@» tolerance).

    A number cell is rendered without the float tail — the criticality group
    arrives as `3.0` while the catalog label is `3` — everything else is
    matched exactly after whitespace/case normalization.
    """
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    cleaned = " ".join(text.replace("\u00a0", " ").split()).casefold()
    return cleaned in known


def _defect_text(value: object) -> str:
    """
    Render a ДВ number as digits, restoring the dropped leading zero.

    The form stores the defect number as a number, so a 7-digit value picked up
    from an Excel cell loses its leading zero (`0706179` → `706179`); the work
    ID rule requires exactly seven digits, so short values are left-padded —
    the same normalisation the edit path applies.
    """
    if value is None or isinstance(value, bool):
        return ""
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
    return text.zfill(7) if text.isdigit() and len(text) < 7 else text


def _trimmed_text(value: object) -> str | None:
    """Text cell, trimmed; an empty result is the same as an empty cell (None)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("\u00a0", " ").strip()
    return text or None


def _coerce(value: object, cell_type: str) -> object | None:
    if cell_type == "text":
        # Text is stored trimmed: stray whitespace (including the non-breaking
        # space Excel loves) must never become part of a typed value. An empty
        # string is the same as an empty cell.
        return _trimmed_text(value)
    if cell_type == "number":
        try:
            number = float(str(value))
        except TypeError, ValueError:
            return None
        # pyxlsb returns full floats: a ДВ number must not become `706179.0`.
        return str(int(number)) if number.is_integer() and abs(number) < 1e15 else number
    if cell_type == "date":
        if isinstance(value, (date, datetime)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        if isinstance(value, (int, float)) and value > 20000:
            return (_EXCEL_EPOCH + timedelta(days=float(value))).isoformat()
        return None
    if cell_type == "boolean":
        return value if isinstance(value, bool) else None
    return value


def _calculate_code(values: dict[str, object], asset_repair_code: str | None) -> RenovationIdResult:
    """Build the work code from the row's OWN values (year comes from the date)."""
    start_date = values.get("col_024")
    return calculate_renovation_id(
        RenovationIdInput(
            start_date=start_date if isinstance(start_date, str) else "",
            asset_repair_code=asset_repair_code,
            direction_code=str(values.get("col_189") or ""),
            bpof_section=str(values.get("col_016") or ""),
            work_category=str(values.get("col_017") or ""),
            defect_number=_defect_text(values.get("col_023")),
            technology_position=str(values.get("col_011") or ""),
            work_type_label=str(values.get("col_018") or ""),
        )
    )
