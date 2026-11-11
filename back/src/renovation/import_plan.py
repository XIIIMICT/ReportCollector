"""Validate a technical-directorate Excel plan without changing the database."""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import chain, islice
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pyxlsb import open_workbook

from src.renovation.constants import R_ASSETS
from src.renovation.form_schema import field_metas, normalize_label
from src.renovation.plan import RReferenceCatalog
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
# Fallback asset column when a drop has no «Наименование ДО»: the legal entity
# is then matched by its name through the same asset dictionary.
_ASSET_NAME_HEADER = "Предприятие"
# The direction code («Лот») is read for the work-code check only, never stored.
LOT_FIELD = "__lot__"
_EXCEL_EPOCH = date(1899, 12, 30)
# Shapes a date takes when the workbook declares the cell/column as TEXT. The
# declared type is not trusted: the customer's files routinely hold real dates
# in text-formatted cells (`05.11.2003`), and those values are correct. Day
# first — the files are Russian; the time part is optional.
_DATE_TEXT_FORMATS: tuple[str, ...] = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)
# Fallback for a fresh database that has no assets yet: the plan files carry
# the official repair code, the UI shows the legal-entity name.
_TEMPLATE_ASSETS: dict[str, str] = {repair_code.upper(): name for _, repair_code, name in R_ASSETS}
_REPAIR_CODES: dict[str, str] = {code.upper(): repair_code.upper() for code, repair_code, _ in R_ASSETS}

# The template is matched by column NAMES only — never by their position: the
# customer adds/removes columns between drops. Columns that are present but not
# in `_TEMPLATE_FIELDS` (year, lot, numbering, pressure/temperature, repair
# months, fact/forecast/reason) are ignored. Only the REQUIRED headings below
# make a file a plan: any other missing heading is simply not loaded.


def _norm(value: object) -> str:
    return normalize_label(str(value or "")).casefold()


def _is_blank(value: object) -> bool:
    """Whether a cell carries no value; whitespace-only text counts as empty."""
    return value is None or (isinstance(value, str) and not value.strip())


# Cached value Excel writes for a formula whose referenced cell/range is gone.
# It arrives as ordinary TEXT through both readers, so without an explicit check
# a text field would silently store «#REF!» and the analysis would call the file
# clean.
_REFERENCE_ERROR_VALUES = frozenset({"#ref!", "#ref"})


def _is_reference_error(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() in _REFERENCE_ERROR_VALUES


# Quote styles Excel writes around a legal-entity name («АО «БСЗ»», `АО "БСЗ"`,
# «АО “БСЗ”»). The file and the dictionary use different ones for the same asset,
# so the name key folds them all to one ordinary double quote.
_QUOTES: dict[int, str] = str.maketrans(
    {"\u00ab": '"', "\u00bb": '"', "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"'}
)


def _asset_name_key(value: object) -> str:
    """
    Match key of an asset NAME: case-, whitespace- and quote-insensitive.

    The files spell «АО "БСЗ"» while the dictionary holds «АО «БСЗ»»: a byte-wise
    comparison would call every such row an unknown asset. Codes use the same
    key (`" bszr "` → `"bszr"`).
    """
    return _norm(str(value or "").translate(_QUOTES))


def _asset_column(header: list[object]) -> tuple[int | None, bool]:
    """
    Locate the asset column: `(index, is_name)`.

    «Наименование ДО» (the repair code) is authoritative when present. A drop
    without it still names the legal entity in «Предприятие», which is matched
    by the asset NAME through the same dictionary — hence the flag.
    """
    code_index = next((i for i, value in enumerate(header) if _norm(value) == _norm(_ASSET_HEADER)), None)
    if code_index is not None:
        return code_index, False
    name_index = next((i for i, value in enumerate(header) if _norm(value) == _norm(_ASSET_NAME_HEADER)), None)
    return name_index, name_index is not None


# Template asset list keyed the way a file spells it: a «Предприятие» drop is
# matched by name, a «Наименование ДО» drop by repair code. Used only when the
# database has no assets yet — the live dictionary wins.
_TEMPLATE_ASSET_NAMES: dict[str, str] = {_asset_name_key(name): name for _, _, name in R_ASSETS}
_REPAIR_CODES_BY_NAME: dict[str, str] = {
    _asset_name_key(name): repair_code.upper() for _, repair_code, name in R_ASSETS
}


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


@dataclass(frozen=True)
class PlanFileScope:
    """Dictionaries an uploaded plan file validates against."""

    # Repair codes («Наименование ДО») present in the sheet.
    assets: frozenset[str]
    # Reference catalogs the file has a column for; only those can report a
    # missing value, so only their changes may invalidate the report.
    catalogs: frozenset[str]
    # When the drop has no «Наименование ДО», `assets` holds asset NAMES (from
    # «Предприятие») instead of codes.
    assets_are_names: bool = False


# A file's «Предприятие» column is the asset dictionary, not a
# `RenovationReferenceValue` catalog: the asset codes are scoped separately.
_REFERENCE_CATALOGS = frozenset(catalog.value for catalog in RReferenceCatalog)


def scan_file_scope(source: Path | BinaryIO, suffix: str | None = None) -> PlanFileScope:
    """
    Asset codes (or names) and reference catalogs present in the workbook's sheet.

    Used to scope the cache key: a plan change in another asset, or an edit to a
    catalog the file has no column for, does not invalidate this file's report.
    Read errors yield an empty scope, which the caller treats as «unknown» (no
    caching).
    """
    empty = PlanFileScope(assets=frozenset(), catalogs=frozenset())
    try:
        rows = _iter_plan_rows(source, suffix)
        first_rows = list(islice(rows, 2))
    except BadZipFile, KeyError, OSError, ValueError, InvalidFileException:
        return empty
    header, data_start = _locate_header(first_rows)
    if header is None:
        return empty
    code_index = next((i for i, value in enumerate(header) if _norm(value) == _norm(_CODE_HEADER)), None)
    asset_index, assets_are_names = _asset_column(header)
    if asset_index is None:
        return empty
    data_rows = chain(first_rows[data_start:], rows)
    keys = {
        _asset_name_key(raw[asset_index])
        for _row_number, raw in _data_rows(data_rows, data_start, code_index)
        if asset_index < len(raw) and raw[asset_index] not in (None, "")
    }
    return PlanFileScope(
        assets=frozenset(key for key in keys if key),
        assets_are_names=assets_are_names,
        catalogs=_catalogs_in_header(header),
    )


def _catalogs_in_header(header: list[object]) -> frozenset[str]:
    """Return the reference catalogs the template columns resolve to (assets excluded)."""
    fields = {field.db_name: field for field in field_metas()}
    mapping = _header_map()
    catalogs: set[str] = set()
    for value in header:
        normalized = _norm(value)
        if not normalized:
            continue
        field = mapping.get(normalized)
        if field is None and normalized.startswith(_norm("Дефектная ведомость/ВОР")):
            field = "col_023"
        meta = fields.get(field) if field is not None else None
        if meta is not None and meta.catalog in _REFERENCE_CATALOGS:
            catalogs.add(meta.catalog)
    return frozenset(catalogs)


def assets_in_file(source: Path | BinaryIO, suffix: str | None = None) -> frozenset[str]:
    """
    Repair codes («Наименование ДО») present in the workbook's plan sheet.

    Kept as the narrow form of `scan_file_scope` for callers that only need the
    codes; the preflight uses the full scope so catalog edits invalidate too.
    """
    return scan_file_scope(source, suffix).assets


def _header_score(row: list[object]) -> int:
    """Count the cells that resolve to the work code, the asset or a form field."""
    mapping = _header_map()
    specials = {_norm(_CODE_HEADER), _norm(_ASSET_HEADER), _norm(_ASSET_NAME_HEADER)}
    return sum(1 for value in row if (key := _norm(value)) and (key in mapping or key in specials))


def _locate_header(rows: list[list[object]]) -> tuple[list[object] | None, int]:
    """
    Return the header row and the first data row index (row 1 or row 2).

    The work-code heading is the template's anchor. When it is absent, the row
    carrying the most other template headings is still treated as the header:
    the report must be able to name the missing required columns instead of
    calling a readable workbook unreadable.
    """
    if not rows:
        return None, 0
    for level in (0, 1):
        if level < len(rows) and any(_norm(value) == _norm(_CODE_HEADER) for value in rows[level]):
            return rows[level], level + 1
    score, level = max(((_header_score(row), level) for level, row in enumerate(rows[:2])), default=(0, 0))
    if score == 0:
        return None, 0
    return rows[level], level + 1


def _column_name(index: int) -> str:
    """Excel letters of a 0-based column index (0 → A, 27 → AB)."""
    result = ""
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


def _is_numbering_row(raw: list[object], code_index: int | None) -> bool:
    """
    Detect the «№ п/п» helper row: a number where the work code belongs.

    The counting row has numbers everywhere — typed or stored as text
    (`"1"`, `"2"`, …, a «Текстовый» column does that). A real data row always has
    a work code (letters/underscores) or none at all, so a numeric value in the
    code column cannot be a data row. The surrounding cells are not consulted:
    the counting row matches the sheet width while a data row may end with a
    stray number, so the code cell alone is the reliable signal.
    """
    if code_index is None or code_index >= len(raw):
        return False
    return _is_number_text(raw[code_index])


def _is_number_text(value: object) -> bool:
    """Whether a cell holds a plain number, either typed or stored as text."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if not isinstance(value, str):
        return False
    text = value.replace("\u00a0", " ").strip().replace(",", ".")
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _data_rows(
    rows: Iterable[list[object]], data_start: int, code_index: int | None
) -> Iterator[tuple[int, list[object]]]:
    """
    Yield `(sheet row number, cells)` for the workbook's data rows.

    Blank rows are dropped, and the counting row directly under the headings is
    dropped when it is the FIRST non-blank row — a blank spacer between the
    header and the numbering row must not hide it. Detecting it here, while the
    sheet row number is known, keeps the report pointing at the real Excel row
    instead of shifting every number by one.
    """
    numbering_pending = True
    for row_number, raw in enumerate(rows, start=data_start + 1):
        # `value is None or value == ""`, not `value not in (None, "")`: the
        # free-threaded build's `in` returns True for an empty string against a
        # tuple holding None, so that idiom silently drops blank-but-valid cells.
        if all(value is None or value == "" for value in raw):
            continue
        if numbering_pending:
            numbering_pending = False
            if _is_numbering_row(raw, code_index):
                continue
        yield row_number, raw


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
# Required columns and the headings the report names them by — the file's own
# wording («Способ выполнения работ»), not the form's shorter label.
_REQUIRED_COLUMN_LABELS: tuple[tuple[str, str], ...] = (
    ("col_015", "Группа критичности оборудования"),
    ("col_017", "Категория работ"),
    ("col_018", "Вид работ"),
    ("col_019", "Отнесение затрат"),
    ("col_020", "Способ выполнения работ"),
    ("col_024", "Дата начала работ"),
    ("col_025", "Дата окончания работ"),
)
# Fields a valid plan row cannot leave empty: the asset, every reference choice
# the plan is grouped by (a blank one cannot be resolved to an option) and the
# work dates. The asset is listed for the row check even though its COLUMN is
# resolved separately (`_asset_column`) — the heading report names
# «Наименование ДО или Предприятие» once.
_REQUIRED_FIELDS: tuple[str, ...] = ("col_002", *(field for field, _ in _REQUIRED_COLUMN_LABELS))
_REQUIRED_ASSET_LABEL = f"{_ASSET_HEADER} или {_ASSET_NAME_HEADER}"


def _missing_required_columns(columns: dict[str, int], code_index: int | None, asset_index: int | None) -> list[str]:
    """
    Human-readable labels of the required headings absent from a readable file.

    A heading counts as present only when the header carries it; an empty cell
    in a present column is a row-level value issue, reported separately.
    """
    missing: list[str] = []
    if code_index is None:
        missing.append(_CODE_HEADER)
    if asset_index is None:
        missing.append(_REQUIRED_ASSET_LABEL)
    missing.extend(label for field, label in _REQUIRED_COLUMN_LABELS if field not in columns)
    return missing


_CATALOG_ALIASES: dict[str, frozenset[str]] = {
    "work_category": frozenset({"окр"}),
    "work_type": frozenset({"до"}),
    "execution_method": frozenset({"собственными силами"}),
}

# The two Excel containers the customer drops: binary and OOXML.
PLAN_FILE_SUFFIXES: tuple[str, ...] = (".xlsb", ".xlsx")


def plan_file_suffix(file_name: str | None) -> str | None:
    """Return the plan workbook's suffix, or None when the name is another format."""
    if not file_name:
        return None
    suffix = Path(file_name).suffix.casefold()
    return suffix if suffix in PLAN_FILE_SUFFIXES else None


def _source_suffix(source: Path | BinaryIO, suffix: str | None) -> str:
    """Resolve the workbook format: the path's suffix, an explicit stream hint, or xlsb."""
    if suffix is not None:
        return suffix.casefold()
    if isinstance(source, Path):
        return source.suffix.casefold()
    return ".xlsb"


def _read_rows(path: Path | BinaryIO, suffix: str | None = None) -> list[list[object]]:
    """Read all worksheet rows for compatibility and small unit-test fixtures."""
    return list(_iter_plan_rows(path, suffix))


def _iter_plan_rows(path: Path | BinaryIO, suffix: str | None = None) -> Iterator[list[object]]:
    """Yield plan-sheet rows without materializing the workbook."""
    if _source_suffix(path, suffix) == ".xlsx":
        yield from _iter_xlsx_rows(path)
        return
    source = str(path) if isinstance(path, Path) else path
    with open_workbook(source) as workbook:
        sheet = next((name for name in workbook.sheets if name.strip().upper().startswith("РБ")), None)
        if sheet is None:
            msg = "no plan sheet"
            raise ValueError(msg)
        with workbook.get_sheet(sheet) as worksheet:
            yield from ([cell.v for cell in row] for row in worksheet.rows())


def _iter_xlsx_rows(source: Path | BinaryIO) -> Iterator[list[object]]:
    """Yield plan-sheet rows from an OOXML workbook, using its cached formula values."""
    workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    try:
        sheet = next((name for name in workbook.sheetnames if name.strip().upper().startswith("РБ")), None)
        if sheet is None:
            msg = "no plan sheet"
            raise ValueError(msg)
        worksheet = workbook[sheet]
        yield from (list(row) for row in worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()


def inspect_plan_file_streaming(
    source: Path | BinaryIO,
    *,
    existing_codes: set[str],
    asset_names_by_code: dict[str, str],
    reference_values: dict[str, set[str]] | None = None,
    suffix: str | None = None,
) -> RenovationPlanImportResult:
    """Inspect an Excel workbook from a row iterator, keeping memory bounded by the report."""
    if isinstance(source, Path) and source.suffix.casefold() not in PLAN_FILE_SUFFIXES:
        return RenovationPlanImportResult(status="invalid_format")
    try:
        return _inspect_rows(
            _iter_plan_rows(source, suffix),
            existing_codes=existing_codes,
            asset_names_by_code=asset_names_by_code,
            reference_values=reference_values,
        )
    except BadZipFile, KeyError, OSError, ValueError, InvalidFileException:
        return RenovationPlanImportResult(status="invalid_format")


def inspect_plan_file(
    source: Path | BinaryIO,
    *,
    existing_codes: set[str],
    asset_names_by_code: dict[str, str],
    reference_values: dict[str, set[str]] | None = None,
    suffix: str | None = None,
) -> RenovationPlanImportResult:
    """Run template, type, duplicate, formula and summary checks."""
    if isinstance(source, Path) and source.suffix.casefold() not in PLAN_FILE_SUFFIXES:
        return RenovationPlanImportResult(status="invalid_format")
    try:
        rows = _read_rows(source, suffix)
        return _inspect_rows(
            iter(rows),
            existing_codes=existing_codes,
            asset_names_by_code=asset_names_by_code,
            reference_values=reference_values,
        )
    except BadZipFile, KeyError, OSError, ValueError, InvalidFileException:
        return RenovationPlanImportResult(status="invalid_format")


def _inspect_rows(
    rows: Iterable[list[object]],
    *,
    existing_codes: set[str],
    asset_names_by_code: dict[str, str],
    reference_values: dict[str, set[str]] | None,
) -> RenovationPlanImportResult:
    """Validate rows from either a materialized fixture or a streaming workbook."""
    rows_iter = iter(rows)
    first_rows = list(islice(rows_iter, 2))
    if not first_rows:
        # An empty plan sheet is readable: every required heading is missing.
        return RenovationPlanImportResult(
            status="has_errors",
            missing_required_columns=_missing_required_columns({}, None, None),
        )
    # Row 1 is the header; some drops prepend an info row, then the header is
    # row 2. A numbering row under the headers (if any) holds numbers only and
    # is skipped by `_is_numbering_row`.
    header_row, data_start = _locate_header(first_rows)
    if header_row is None:
        # The workbook opened, but the sheet carries none of the plan headings:
        # this is a content error, not an unreadable container.
        return RenovationPlanImportResult(
            status="has_errors",
            missing_required_columns=_missing_required_columns({}, None, None),
        )
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
    code_index = next((i for i, value in enumerate(header_row) if _norm(value) == _norm(_CODE_HEADER)), None)
    asset_index, assets_are_names = _asset_column(header_row)
    missing_columns = _missing_required_columns(columns, code_index, asset_index)
    if missing_columns:
        # A column that is absent entirely cannot be skipped row by row: the
        # file does not describe the plan, so it is reported as a whole.
        return RenovationPlanImportResult(
            status="has_errors",
            missing_required_columns=missing_columns,
            columns=_loaded_column_labels(columns),
        )
    assert code_index is not None  # noqa: S101 - the missing-columns check just refused the file
    assert asset_index is not None  # noqa: S101 - same
    data_rows: Iterable[list[object]] = chain(first_rows[data_start:], rows_iter)
    # The asset column is the row's own foreign key, never a plan column: it is
    # read here and resolved against the dictionary, not through `columns`.
    required_columns = tuple(
        index for field, index in columns.items() if field in _REQUIRED_FIELDS and field != "col_002"
    )
    name_by_key = {_asset_name_key(name): name for name in asset_names_by_code.values()}
    repair_code_by_name = {_asset_name_key(name): code for code, name in asset_names_by_code.items()}

    type_errors: list[RenovationPlanImportCellIssue] = []
    formula_reference_errors: list[RenovationPlanImportCellIssue] = []
    missing: list[RenovationPlanImportCellIssue] = []
    missing_required_values: list[RenovationPlanImportCellIssue] = []
    skipped_rows: list[int] = []
    data_row_count = 0
    unknown_assets: list[RenovationPlanImportCellIssue] = []
    unknown_catalogs: set[str] = set()
    assets: set[str] = set()
    codes: list[str] = []
    valid_rows: list[dict[str, object]] = []
    asset_repair_codes: list[str | None] = []
    row_numbers: list[int] = []
    field_by_name = {field.db_name: field for field in field_metas()}
    for row_number, raw in _data_rows(data_rows, data_start, code_index):
        data_row_count += 1
        asset_cell = raw[asset_index] if asset_index < len(raw) else None
        asset_key = _asset_name_key(asset_cell)
        if not asset_key:
            # The asset is the row's foreign key: without it the row cannot be
            # attributed to the plan. This is the ONLY skip criterion, and the
            # skip is not an error unless every data row lacks the asset.
            skipped_rows.append(row_number)
            continue
        code = str(raw[code_index]).strip() if code_index < len(raw) and raw[code_index] is not None else ""
        empty_required = [index for index in required_columns if index >= len(raw) or _is_blank(raw[index])]
        if not code:
            empty_required.insert(0, code_index)
        if empty_required:
            # A blank cell in a present required column is an error: the row is
            # not written and the file must be fixed before it can be loaded.
            missing_required_values.extend(_empty_cell_issue(index, row_number) for index in empty_required)
            continue
        codes.append(code)
        row_numbers.append(row_number)
        # In «Предприятие» mode the cell holds the legal-entity NAME; the repair
        # code is then derived for the (archived) ID check only.
        repair_code = asset_key.upper()
        if assets_are_names:
            # The cell holds the legal-entity name: the dictionary turns it into
            # its canonical spelling (quotes included), with a template fallback
            # for a database whose asset list is still empty.
            asset_name = name_by_key.get(asset_key) or _TEMPLATE_ASSET_NAMES.get(asset_key, "")
            repair_code = repair_code_by_name.get(asset_key) or _REPAIR_CODES_BY_NAME.get(asset_key, "")
        else:
            asset_name = asset_names_by_code.get(_REPAIR_CODES.get(repair_code, repair_code), "")
        if asset_name:
            assets.add(asset_name)
        else:
            # The asset is a reference value like any catalog label: a code the
            # project's dictionary does not have makes the file invalid — it is
            # never silently dropped from the load.
            unknown_assets.append(
                RenovationPlanImportCellIssue(
                    location=f"{_column_name(asset_index)}, строка {row_number}", value=str(asset_cell)
                )
            )
            if not assets_are_names and (default_name := _TEMPLATE_ASSETS.get(asset_key.upper())) is not None:
                # Still counted among the file's assets: the cache scope must
                # cover the rows a fixed file would touch.
                assets.add(default_name)
        if RENOVATION_ID_FORMULA_ENABLED:
            asset_repair_codes.append(repair_code or None)
        values: dict[str, object] = {}
        for field, index in columns.items():
            # Whitespace-only text is EMPTY, exactly like an empty cell: the
            # text cells are stored trimmed, so a blank must never reach the
            # type check (there it became «неверный тип данных», even for an
            # optional field like «Влияние на производство»).
            if index >= len(raw) or _is_blank(raw[index]):
                continue
            meta = field_by_name.get(field)
            if meta is None:
                continue
            # Calculated columns are not stored, but a broken reference there is
            # still a broken formula the operator must see — check BEFORE the
            # calculated-field skip.
            if _is_reference_error(raw[index]):
                formula_reference_errors.append(
                    RenovationPlanImportCellIssue(
                        location=f"{_column_name(index)}, строка {row_number}",
                        value=str(raw[index]).strip(),
                    )
                )
                continue
            if meta.is_calculated:
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
        if RENOVATION_ID_FORMULA_ENABLED:
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
        for row_number, code, values, asset_repair_code in zip(
            row_numbers, codes, valid_rows, asset_repair_codes, strict=False
        ):
            try:
                expected = _calculate_code(values, asset_repair_code)
            except RenovationIdError:
                # An unassemblable row (e.g. an empty «Лот») is an invalid code.
                invalid_codes.append(f"{code} (строка {row_number})")
                continue
            if expected.value != code:
                invalid_codes.append(f"{code} (строка {row_number})")
    all_rows_skipped = bool(data_row_count) and len(skipped_rows) == data_row_count
    status = (
        "has_errors"
        if (
            duplicates
            or invalid_codes
            or type_errors
            or formula_reference_errors
            or missing
            or missing_required_values
            or unknown_assets
            or all_rows_skipped
        )
        else "ready"
    )
    return RenovationPlanImportResult(
        status=status,
        duplicate_codes=_duplicates_with_rows(codes, row_numbers),
        invalid_calculated_codes=_sorted_without_duplicates(invalid_codes),
        invalid_data_types=type_errors,
        formula_reference_errors=formula_reference_errors,
        missing_required_values=missing_required_values,
        skipped_rows=sorted(skipped_rows),
        all_rows_skipped=all_rows_skipped,
        missing_dictionary_values=missing,
        unknown_assets=unknown_assets,
        assets=sorted(assets),
        new_rows=sum(code not in existing_codes for code in codes),
        rows_to_update=sum(code in existing_codes for code in codes),
        columns=_loaded_column_labels(columns),
    )


def _empty_cell_issue(index: int, row_number: int) -> RenovationPlanImportCellIssue:
    """Build the report entry for a required cell left empty in a data row."""
    return RenovationPlanImportCellIssue(location=f"{_column_name(index)}, строка {row_number}", value="")


def _loaded_column_labels(columns: dict[str, int]) -> list[str]:
    """
    Form labels of the mapped template columns, in workbook order.

    `columns` maps a resolved header to its sheet index; the order is how the
    template presents them, which is what the operator compares against the file.
    Calculated fields are skipped: the load never writes them.
    """
    by_field = {meta.db_name: meta for meta in field_metas()}
    labels: list[str] = []
    for field, _index in sorted(columns.items(), key=lambda pair: pair[1]):
        meta = by_field.get(field)
        if meta is None or meta.is_calculated:
            continue
        labels.append(meta.label)
    return labels


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


def normalized_option(value: object) -> str:
    """Render a reference cell as its catalog label: "3.0" ? "3", NBSP ? space."""
    text = " ".join(str(value).replace("\u00a0", " ").split())
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    return text


def _value_in_catalog(known: set[str], value: object) -> bool:
    """
    Strict form-catalog lookup (no typo or «@» tolerance).

    A number cell loses its float tail — the criticality group arrives as `3.0`
    (or the text «3,0») while the catalog label is `3` — everything else is
    matched exactly after whitespace/case normalization.
    """
    return normalized_option(value).casefold() in known


def _defect_text(value: object) -> str:
    """Render a ДВ number as digits for the work-code check."""
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


def _text_date(value: str) -> str | None:
    """
    Parse a date stored as text into ISO text (None when it is not a date).

    The workbook's declared type is unreliable: a correct date often arrives as
    plain text because the column — or just the cell — is formatted as «Текстовый»
    (`05.11.2003`, `5.11.03`, an ISO string, optionally with a time). Rejecting
    those would block a file whose values are fine, so the text is parsed here
    instead of being reported as a wrong data type. Only a real calendar date
    passes: `31.02.2003` and arbitrary text stay type errors.

    The non-breaking space is normalized and runs of whitespace are collapsed
    before matching, the same tolerance the text cells get.
    """
    text = " ".join(value.replace("\u00a0", " ").split())
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    for date_format in _DATE_TEXT_FORMATS:
        try:
            # A date has no time zone: only the calendar day is kept.
            parsed = datetime.strptime(text, date_format)  # noqa: DTZ007
        except ValueError:
            continue
        return parsed.date().isoformat()
    return None


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
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, (int, float)) and value > 20000:
            return (_EXCEL_EPOCH + timedelta(days=float(value))).isoformat()
        if isinstance(value, str):
            return _text_date(value)
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
