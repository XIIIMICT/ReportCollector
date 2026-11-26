"""Validate a technical-directorate Excel plan without changing the database."""

import heapq
import struct
import tempfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import chain, islice
from pathlib import Path
from typing import BinaryIO, cast
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pyxlsb import open_workbook

from src.renovation.constants import R_ASSETS
from src.renovation.form_schema import RenovationFieldMeta, field_metas, normalize_label
from src.renovation.plan import RReferenceCatalog
from src.renovation.reference_data import REFERENCE_OPTIONS
from src.renovation.renovation_id import (
    DIRECTION_CODE_FIELD,
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
    _norm("Наименование производства"): "production_name",
    _norm("Цех"): "workshop_name",
    _norm("Отделение/установка"): "department_or_installation_name",
    _norm("Класс оборудования"): "equipment_class",
    _norm("Вид (категория) оборудования"): "equipment_category",
    _norm("Наименование ЕО"): "equipment_unit_name",
    _norm("Название технического места"): "technical_location_name",
    _norm("Технологическая позиция"): "process_position",
    _norm("Инвентарный №"): "inventory_number",
    _norm("Функциональное направление"): "functional_area",
    _norm("Влияние на производство"): "production_impact",
    _norm("Группа критичности оборудования"): "equipment_criticality_group_id",
    _norm("Раздел БПОФ"): "bpof_section",
    _norm("Категория работ"): "work_category_id",
    _norm("Вид работ"): "work_type_id",
    _norm("Отнесение затрат"): "cost_allocation_type_id",
    _norm("Способ выполнения работ"): "execution_method_id",
    _norm("Содержание работ"): "work_description",
    _norm(
        "Дефектная ведомость/ВОР № (в случаях когда инв.№ распространяется на отделение "
        "или установку, или блок из нескольких едениц оборудования)"
    ): "defect_list_or_bill_of_quantities_number",
    _norm("Дата начала работ"): "work_start_date",
    _norm("Дата окончания работ"): "work_end_date",
    _norm("Трудозатраты чел.час"): "labor_hours",
    _norm("Работа, руб. без ндс"): "work_cost_rub_ex_vat",
    _norm("МТР Заказчика, руб. без НДС"): "customer_materials_cost_rub_ex_vat",
    _norm("МТР Подрядчик, руб. без НДС"): "contractor_supplied_materials_cost_rub_ex_vat",
    _norm("Стоимость итого, руб. без НДС"): "total_cost_rub_ex_vat",
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
            field = "defect_list_or_bill_of_quantities_number"
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
    mapping[_norm("Отнесение затрат")] = "cost_allocation_type_id"
    mapping[_norm("Способ выполнения работ")] = "execution_method_id"
    mapping[_norm("Лот")] = "lotting_activity_direction_code"
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
    ("equipment_criticality_group_id", "Группа критичности оборудования"),
    ("work_category_id", "Категория работ"),
    ("work_type_id", "Вид работ"),
    ("cost_allocation_type_id", "Отнесение затрат"),
    ("execution_method_id", "Способ выполнения работ"),
    ("work_start_date", "Дата начала работ"),
    ("work_end_date", "Дата окончания работ"),
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
    issue_limit: int = 1000,
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
            issue_limit=issue_limit,
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
    issue_limit: int = 1000,
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
            issue_limit=issue_limit,
        )
    except BadZipFile, KeyError, OSError, ValueError, InvalidFileException:
        return RenovationPlanImportResult(status="invalid_format")


class _CappedExamples[T]:
    """Exact counter with at most `limit` actionable examples retained."""

    __slots__ = ("_limit", "count", "items")

    def __init__(self, limit: int) -> None:
        self._limit = max(limit, 0)
        self.count = 0
        self.items: list[T] = []

    def add(self, make: Callable[[], T]) -> None:
        """Count every occurrence, but build an example only while under the cap."""
        self.count += 1
        if len(self.items) < self._limit:
            self.items.append(make())


# Row numbers kept in a duplicate example; the rest are counted, not materialized.
_MAX_EXAMPLE_ROWS = 10


def _format_duplicate(code: str, rows: list[int], hidden: int = 0) -> str:
    """Render one duplicate code with the sheet rows it occupies."""
    shown = ", ".join(str(number) for number in rows[:_MAX_EXAMPLE_ROWS])
    extra = f" и ещё {hidden}" if hidden else ""
    return f"{code} (строки {shown}{extra})"


class _CodeRowSpool:
    """
    Whole-file duplicate detection with O(run) memory instead of O(rows).

    `(code, row)` pairs are written to OS-backed temporary files in sorted runs
    and then k-way merged, so a file of any size reports exact duplicate and
    new/update totals while keeping only bounded examples in RAM. The files are
    deleted when the spool is closed (also on failure/cancellation).
    """

    _RUN_SIZE = 20_000

    def __init__(self, existing_codes: set[str]) -> None:
        self._existing_codes = existing_codes
        self._buffer: list[tuple[str, int]] = []
        self._runs: list[BinaryIO] = []
        self.new_rows = 0
        self.rows_to_update = 0

    def add(self, code: str, row_number: int) -> None:
        if code in self._existing_codes:
            self.rows_to_update += 1
        else:
            self.new_rows += 1
        self._buffer.append((code, row_number))
        if len(self._buffer) >= self._RUN_SIZE:
            self._flush_run()

    def _flush_run(self) -> None:
        if not self._buffer:
            return
        self._buffer.sort()
        # Kept open across calls so the merge can stream it; closed in close().
        # Length-prefixed binary records are cheaper than JSON lines at this size.
        run = cast("BinaryIO", tempfile.TemporaryFile(mode="w+b"))  # noqa: SIM115
        try:
            for code, row_number in self._buffer:
                encoded = code.encode("utf-8")
                run.write(struct.pack("!I", len(encoded)))
                run.write(encoded)
                run.write(struct.pack("!Q", row_number))
        except BaseException:
            run.close()
            raise
        run.seek(0)
        self._runs.append(run)
        self._buffer.clear()

    @staticmethod
    def _read_run(run: BinaryIO) -> Iterator[tuple[str, int]]:
        while header := run.read(4):
            length = struct.unpack("!I", header)[0]
            payload = run.read(length + 8)
            yield payload[:length].decode("utf-8"), struct.unpack("!Q", payload[length:])[0]

    def _sorted_pairs(self) -> Iterator[tuple[str, int]]:
        streams = [self._read_run(run) for run in self._runs]
        if self._buffer:
            streams.append(iter(sorted(self._buffer)))
        yield from heapq.merge(*streams)

    def result(self, issue_limit: int) -> tuple[list[str], int]:
        """Return (bounded examples, exact count of distinct repeated codes)."""
        examples: list[str] = []
        duplicates = 0
        current: str | None = None
        rows: list[int] = []
        total_rows = 0
        for code, row_number in self._sorted_pairs():
            if code != current:
                self._finish(current, rows, total_rows - len(rows), examples, issue_limit)
                duplicates += current is not None and total_rows > 1
                current, rows, total_rows = code, [], 0
            total_rows += 1
            if len(rows) < _MAX_EXAMPLE_ROWS:
                rows.append(row_number)
        self._finish(current, rows, total_rows - len(rows), examples, issue_limit)
        if current is not None:
            duplicates += total_rows > 1
        return examples, duplicates

    @staticmethod
    def _finish(code: str | None, rows: list[int], hidden: int, examples: list[str], issue_limit: int) -> None:
        if code is None or len(rows) < 2:
            return
        if len(examples) < issue_limit:
            examples.append(_format_duplicate(code, rows, hidden))

    def close(self) -> None:
        self._buffer.clear()
        for run in self._runs:
            run.close()
        self._runs.clear()


def _inspect_rows(
    rows: Iterable[list[object]],
    *,
    existing_codes: set[str],
    asset_names_by_code: dict[str, str],
    reference_values: dict[str, set[str]] | None,
    issue_limit: int = 1000,
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
            field = "defect_list_or_bill_of_quantities_number"
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

    # Counters are incremented as cells are read; only the first `issue_limit`
    # examples are ever materialized. Nothing is collected and sliced later.
    type_errors = _CappedExamples(issue_limit)
    invalid_values = _CappedExamples(issue_limit)
    formula_reference_errors = _CappedExamples(issue_limit)
    missing = _CappedExamples(issue_limit)
    missing_required_values = _CappedExamples(issue_limit)
    skipped = _CappedExamples(issue_limit)
    unknown_assets = _CappedExamples(issue_limit)
    # Whole-file duplicate detection (and the new/update totals) are disk-backed:
    # a file of any size keeps exact counts without an O(rows) Python collection.
    spool = _CodeRowSpool(existing_codes)
    data_row_count = 0
    unknown_catalogs: set[str] = set()
    assets: set[str] = set()
    field_by_name = {field.db_name: field for field in field_metas()}
    label_by_index = {
        index: meta.label for field, index in columns.items() if (meta := field_by_name.get(field)) is not None
    }
    label_by_index[code_index] = _CODE_HEADER
    label_by_index[asset_index] = _ASSET_NAME_HEADER if assets_are_names else _ASSET_HEADER

    def cell_issue(index: int, row_number: int, value: str) -> RenovationPlanImportCellIssue:
        return RenovationPlanImportCellIssue(
            location=f"{_column_name(index)}, строка {row_number}",
            value=value,
            column_label=label_by_index.get(index),
        )

    try:
        for row_number, raw in _data_rows(data_rows, data_start, code_index):
            data_row_count += 1
            asset_cell = raw[asset_index] if asset_index < len(raw) else None
            asset_key = _asset_name_key(asset_cell)
            if not asset_key:
                # The asset is the row's foreign key: without it the row cannot be
                # attributed to the plan. This is the ONLY skip criterion, and the
                # skip is not an error unless every data row lacks the asset.
                skipped.add(lambda number=row_number: number)
                continue
            code = str(raw[code_index]).strip() if code_index < len(raw) and raw[code_index] is not None else ""
            empty_required = [index for index in required_columns if index >= len(raw) or _is_blank(raw[index])]
            if not code:
                empty_required.insert(0, code_index)
            if empty_required:
                # A blank cell in a present required column is an error: the row is
                # not written and the file must be fixed before it can be loaded.
                for index in empty_required:
                    missing_required_values.add(lambda idx=index, number=row_number: cell_issue(idx, number, ""))
                continue
            spool.add(code, row_number)
            # In «Предприятие» mode the cell holds the legal-entity NAME. The
            # repair code was needed only by the archived ID check (now off).
            if assets_are_names:
                asset_name = name_by_key.get(asset_key) or _TEMPLATE_ASSET_NAMES.get(asset_key, "")
            else:
                repair_code = asset_key.upper()
                asset_name = asset_names_by_code.get(_REPAIR_CODES.get(repair_code, repair_code), "")
            if asset_name:
                assets.add(asset_name)
            else:
                # The asset is a reference value like any catalog label: a code the
                # project's dictionary does not have makes the file invalid — it is
                # never silently dropped from the load.
                unknown_assets.add(
                    lambda number=row_number, cell=asset_cell: cell_issue(asset_index, number, str(cell))
                )
                if not assets_are_names and (default_name := _TEMPLATE_ASSETS.get(asset_key.upper())) is not None:
                    # Still counted among the file's assets: the cache scope must
                    # cover the rows a fixed file would touch.
                    assets.add(default_name)
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
                    formula_reference_errors.add(
                        lambda idx=index, number=row_number, cells=raw: cell_issue(idx, number, str(cells[idx]).strip())
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
                    if meta.cell_type == "number":
                        value, problem = _number_value(raw[index], meta)
                        if value is None:
                            if problem is None:
                                type_errors.add(
                                    lambda idx=index, number=row_number, cells=raw: cell_issue(
                                        idx, number, str(cells[idx])
                                    )
                                )
                            else:
                                invalid_values.add(
                                    lambda idx=index, number=row_number, cells=raw: cell_issue(
                                        idx, number, str(cells[idx])
                                    )
                                )
                            continue
                    else:
                        value = _coerce(raw[index], meta.cell_type)
                        if value is None:
                            type_errors.add(
                                lambda idx=index, number=row_number, cells=raw: cell_issue(idx, number, str(cells[idx]))
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
                        missing.add(
                            lambda idx=index, number=row_number, value=label: cell_issue(idx, number, str(value))
                        )
        duplicate_codes, duplicate_count = spool.result(issue_limit)
        new_rows, rows_to_update = spool.new_rows, spool.rows_to_update
    finally:
        spool.close()
    # ARCHIVED: the «correctly calculated code» check stays off. The renovation-ID
    # formula is disabled and codes are imported as typed, so every hand-edited code
    # would be flagged. Restoring it also requires reinstating the row-value spool.
    invalid_code_count = 0
    all_rows_skipped = bool(data_row_count) and skipped.count == data_row_count
    status = (
        "has_errors"
        if (
            duplicate_count
            or invalid_code_count
            or type_errors.count
            or invalid_values.count
            or formula_reference_errors.count
            or missing.count
            or missing_required_values.count
            or unknown_assets.count
            or all_rows_skipped
        )
        else "ready"
    )
    # Phase 9: exact totals survive truncation. The UI can say «и ещё N» and
    # the operator still knows the file was not silently accepted.
    issue_counts = {
        "duplicate_codes": duplicate_count,
        "invalid_calculated_codes": invalid_code_count,
        "invalid_data_types": type_errors.count,
        "invalid_values": invalid_values.count,
        "formula_reference_errors": formula_reference_errors.count,
        "missing_required_values": missing_required_values.count,
        "missing_dictionary_values": missing.count,
        "unknown_assets": unknown_assets.count,
        "skipped_rows": skipped.count,
    }
    truncated = any(count > issue_limit for count in issue_counts.values())

    return RenovationPlanImportResult(
        status=status,
        duplicate_codes=duplicate_codes,
        invalid_calculated_codes=[],
        invalid_data_types=type_errors.items,
        invalid_values=invalid_values.items,
        formula_reference_errors=formula_reference_errors.items,
        missing_required_values=missing_required_values.items,
        skipped_rows=sorted(skipped.items),
        all_rows_skipped=all_rows_skipped,
        missing_dictionary_values=missing.items,
        unknown_assets=unknown_assets.items,
        assets=sorted(assets),
        new_rows=new_rows,
        rows_to_update=rows_to_update,
        columns=_loaded_column_labels(columns),
        issue_counts=issue_counts,
        truncated=truncated,
    )


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
        _format_duplicate(code, numbers, len(numbers) - len(numbers[:_MAX_EXAMPLE_ROWS]))
        for code, numbers in sorted(rows_by_code.items())
        if len(numbers) > 1
    ]


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


def _parse_number(value: object) -> Decimal | None:
    """
    Parse a numeric cell, including the text shapes Excel leaves behind.

    Russian files write decimals with a comma and thousands with spaces
    («1 234,50»); a text-formatted number must not become «неверный тип».
    When both separators are present, the LAST one is the decimal mark and the
    other is a thousands separator.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int | float):
        number = Decimal(str(value))
        return number if number.is_finite() else None
    if not isinstance(value, str):
        return None
    text = value.replace("\u00a0", " ").replace("\u202f", " ").strip()
    if not text:
        return None
    text = text.replace(" ", "")
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        text = text.replace(".", "").replace(",", ".") if last_comma > last_dot else text.replace(",", "")
    elif last_comma >= 0:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _number_rule_problem(number: Decimal, meta: RenovationFieldMeta) -> str | None:
    """Return the broken numeric rule («integer»/«range») or None."""
    if (
        meta.decimals == 0 or any(rule.get("kind") == "integer" for rule in meta.rules)
    ) and number != number.to_integral_value():
        return "integer"
    for rule in meta.rules:
        kind = rule.get("kind")
        if kind in {"minimum", "maximum"}:
            bound = rule.get("value")
            if bound is None:
                continue
            limit = Decimal(str(bound))
            if (kind == "minimum" and number < limit) or (kind == "maximum" and number > limit):
                return "range"
        elif kind == "percentage":
            minimum = Decimal(str(rule.get("minimum", 0)))
            maximum = Decimal(str(rule.get("maximum", 100)))
            if not minimum <= number <= maximum:
                return "range"
    return None


def _number_value(value: object, meta: RenovationFieldMeta) -> tuple[object | None, str | None]:
    """
    Coerce a numeric cell and enforce the field's own numeric rules.

    `(None, None)` is a type error (not a number); `(None, reason)` is a value
    error (fraction in an integer column, out of bounds).
    """
    number = _parse_number(value)
    if number is None:
        return None, None
    problem = _number_rule_problem(number, meta)
    if problem is not None:
        return None, problem
    if number == number.to_integral_value() and abs(number) < Decimal("1e15"):
        return str(int(number)), None
    return float(number), None


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
    start_date = values.get("work_start_date")
    return calculate_renovation_id(
        RenovationIdInput(
            start_date=start_date if isinstance(start_date, str) else "",
            asset_repair_code=asset_repair_code,
            direction_code=str(values.get("lotting_activity_direction_code") or ""),
            bpof_section=str(values.get("bpof_section") or ""),
            work_category=str(values.get("work_category_id") or ""),
            defect_number=_defect_text(values.get("defect_list_or_bill_of_quantities_number")),
            technology_position=str(values.get("process_position") or ""),
            work_type_label=str(values.get("work_type_id") or ""),
        )
    )
