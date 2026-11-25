"""
Excel export of the «Ремонт» renovation plan (plain workbook, no customer template).

The export mirrors the legacy sheet layout: a merged 11-row header built
from the form's headerCells (levels 0..10, Excel ranges B6:HI16 in the
template, shifted up so the header starts at row 1), column A as the row
number, then the 217 form fields (from the synthetic asset column through the
last leaf) in columns B..HI.
Reference fields are written as display labels, numbers keep their
decimal count, dates as DD.MM.YYYY, booleans as Да/Нет.

The package mechanics (static parts, cell XML, merges) are shared from
``src.common.xlsx_stream``; this module owns the plan's layout and the
aggregating band only.
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, UnsupportedOperation
from queue import Queue
from threading import Thread
from typing import IO, TYPE_CHECKING, cast
from uuid import UUID
from xml.sax.saxutils import escape as xml_specials_escape
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from pydantic import BaseModel

from src.common import xlsx_stream
from src.common.export import (
    ExportValidationError,
    safe_sheet_name,
    validate_columns_allowed,
    validate_export_shape,
)
from src.common.xlsx_table import TableWorkbookWriter
from src.renovation.form_schema import RenovationFieldMeta, catalog_labels, field_metas, header_rows
from src.renovation.plan import RPlanState

if TYPE_CHECKING:
    from src.renovation.schemas import RenovationExportViewRequest
    from src.renovation.service import RenovationPlanExportData

# The headerCells excelRange rows are the template's (6..16); the export
# starts the header at row 1, so every range shifts up by this amount.
HEADER_ROW_SHIFT = 5


def stream_renovation_workbook(plan: RenovationPlanExportData) -> Iterator[bytes]:
    """Yield the XLSX while it is generated so proxies receive an early byte."""
    queue: Queue[bytes | BaseException | None] = Queue(maxsize=8)

    def build() -> None:
        try:
            sink = _QueueWriter(queue)
            writer = RenovationWorkbookWriter(
                cast("IO[bytes]", sink),
                build_plan_sheet_layout(plan.block, plan.totals, values_only=plan.values_only),
                plan.reference_labels,
                totals=plan.totals,
                row_total=len(plan.rows),
            )
            writer.write_rows(plan.rows)
            writer.close()
        except BaseException as error:  # noqa: BLE001
            queue.put(error)
        finally:
            queue.put(None)

    Thread(target=build, name="renovation-xlsx", daemon=True).start()
    while True:
        chunk = queue.get()
        if chunk is None:
            break
        if isinstance(chunk, BaseException):
            raise chunk
        yield chunk


class _QueueWriter:
    """Minimal non-seekable binary stream accepted by zipfile's data-descriptor mode."""

    def __init__(self, queue: Queue[bytes | BaseException | None]) -> None:
        self.queue = queue
        self.offset = 0

    def write(self, data: bytes) -> int:
        if data:
            self.queue.put(data)
            self.offset += len(data)
        return len(data)

    def tell(self) -> int:
        return self.offset

    def seek(self, *_args: object) -> int:
        raise UnsupportedOperation

    def flush(self) -> None:
        pass


_ALWAYS_VISIBLE_FIELDS = frozenset({"work_or_repair_code", "note"})
_ASSET_FIELD = "col_002"

# May surface to the user through the export job's status - hence Russian.
_ROW_COUNT_MISMATCH_MESSAGE = (
    "Не удалось сформировать файл: количество строк плана изменилось во время выгрузки. Повторите попытку."
)
_EXPORT_TOO_LARGE_MESSAGE = (
    "Выгрузка превышает 4 ГБ — Excel не сможет открыть такой файл. "
    "Сузьте область выгрузки (блок, предприятие или набор строк)."
)


class ExportRowCountMismatchError(ValueError):
    """The plan's exported row count changed while the file was generated."""


class ExportTooLargeError(RuntimeError):
    """
    The sheet part outgrew the plain-ZIP entry ceiling (4 GiB uncompressed).

    The append-only container deliberately stays off zip64: a streamed zip64
    entry's zeroed local-header extra is rejected by real Excel. A sheet past
    the ceiling therefore cannot be streamed at all — the write fails cleanly
    instead of producing a file Excel would repair.
    """


def plan_export_filename(state: RPlanState, stamp: datetime) -> str:
    """
    «Наименование — <версия> (дд.мм.гггг, чч:мм:сс).xlsx», как остальные формы.

    The timestamp is the export stamp resolved by the service: the submit/approve
    moment for a pending/archived version and the max row.updated_at (last edit)
    for the working one.
    """
    suffix = {
        RPlanState.WORKING: "рабочая версия",
        RPlanState.PENDING_APPROVAL: "версия на согласовании",
        RPlanState.ARCHIVED: "утверждённая версия",
    }.get(state, "версия")
    timestamp = stamp.astimezone().strftime("%d.%m.%Y, %H.%M.%S")
    return f"Пообъектный план — {suffix} ({timestamp}).xlsx"


def _export_fields(block: object) -> tuple[RenovationFieldMeta, ...]:
    """
    Form fields the export keeps for a block filter (mirrors the grid).

    `col_002` stays as the asset column (its header labels the asset, its data
    comes from the joined asset name); `work_or_repair_code` and `note` also stay visible
    for every block. A block keeps only its own fields plus those.
    """
    fields = list(field_metas())
    if block is None:
        return tuple(fields)
    block_value = getattr(block, "value", block)
    return tuple(
        meta
        for meta in fields
        if meta.db_name == _ASSET_FIELD
        or meta.db_name in _ALWAYS_VISIBLE_FIELDS
        or meta.responsibility_block == block_value
    )


def _column_offset(meta: RenovationFieldMeta) -> int:
    """Original 1-based Excel column of a field in the legacy layout."""
    # `position` is the 1-based field index (col_002 = 1); the legacy template
    # maps it to Excel column B (2), with column A as the synthetic row number.
    return meta.position + 1


# Placeholder replaced with the data row number when a formula template is
# rendered. NUL cannot appear in a formula string from the form JSON.
_ROW_PLACEHOLDER = "\x00"


@dataclass(slots=True)
class PlanSheetLayout:
    """
    Per-export sheet pieces shared by every data row.

    Column letters and formula templates are row-independent: compiling them
    once removes millions of `get_column_letter`/regex calls from the row loop
    (the dominant cost of a 300k-row export).
    """

    fields: tuple[RenovationFieldMeta, ...]
    column_by_field: dict[str, int]
    letters: dict[int, str]
    # field -> Excel formula template carrying `_ROW_PLACEHOLDER` (absent — no
    # formula: a plain field or a calculated one whose dependency was dropped).
    # The template is XML-escaped ONCE here: the row loop then only substitutes
    # the row number (escaping ~130 formulas per row was a top export cost).
    formulas: dict[str, str]
    widths_xml: str
    header_xml: str
    merged_xml: str
    header_rows: int
    values_only: bool = True


def build_plan_sheet_layout(
    block: object, totals: dict[str, float] | None = None, *, values_only: bool = True
) -> PlanSheetLayout:
    """
    Precompute the static sheet parts for a block-filtered export.

    The sheet has NO row-number column (the grid's «№» stays on screen; the
    exported workbook starts directly with the «Предприятие» column, like
    every other table export).
    """
    fields = _export_fields(block)
    column_by_field = {meta.db_name: 1 + index for index, meta in enumerate(fields)}
    letters = {column: get_column_letter(column) for column in range(1, len(fields) + 1)}
    formulas: dict[str, str] = {}
    if not values_only:
        for meta in fields:
            if not (meta.is_calculated and meta.formula):
                continue
            template = _compile_formula_template(meta.formula, column_by_field, letters)
            if template is not None:
                # Escaped once - and with plain specials escaping (&, <, >), NOT
                # `xlsx_stream.xml_escape`: that would strip the \x00 row
                # placeholders the template still carries (NUL is illegal in the
                # FINAL XML but is substituted with the row number before writing).
                # The row loop only substitutes the row number afterwards.
                formulas[meta.db_name] = xml_specials_escape(template)
    return PlanSheetLayout(
        fields=fields,
        column_by_field=column_by_field,
        letters=letters,
        formulas=formulas,
        widths_xml=_column_widths_xml(fields, column_by_field),
        header_xml=_header_rows_xml(fields, column_by_field, letters),
        merged_xml=_merged_ranges_xml(fields, column_by_field, totals),
        header_rows=len(header_rows()),
        values_only=values_only,
    )


def _header_rows_xml(
    fields: tuple[RenovationFieldMeta, ...],
    column_by_field: dict[str, int],
    letters: dict[int, str],
) -> str:
    parts: list[str] = []
    for row_number, values in enumerate(_header_matrix(fields, column_by_field), start=1):
        xml = [f'<row r="{row_number}" ht="30" customHeight="1">']
        for column, value in enumerate(values, start=1):
            ref = f"{letters[column]}{row_number}"
            # A merged header range is expressed by a top-left <c> plus the
            # <mergeCells> entry; every other cell of the range still needs the
            # bordered style, or Excel draws the frame only around the top-left
            # cell and the grid looks underdrawn down and right.
            if value is None:
                xml.append(xlsx_stream.styled_cell_xml(ref, style=1))
            else:
                xml.append(xlsx_stream.text_cell_xml(ref, value, style=1))
        xml.append("</row>")
        parts.append("".join(xml))
    return "".join(parts)


def _number_row_xml(layout: PlanSheetLayout) -> str:
    """
    Render the column-number strip (sheet row 12) - the grid's numbering.

    Every exported column carries its workbook ordinal shifted so the asset
    column («Предприятие») is 1 - the same number the grid's strip shows.
    There is no corner cell: the sheet starts with the asset column, whose
    number cell merges down over the totals row like every non-aggregating
    column (see `_merged_ranges_xml`).
    """
    row_number = layout.header_rows + 1
    cells = []
    for index, meta in enumerate(layout.fields):
        ref = f"{layout.letters[index + 1]}{row_number}"
        cells.append(xlsx_stream.text_cell_xml(ref, str(meta.ordinal - 1), style=5))
    return f'<row r="{row_number}" ht="20" customHeight="1">{"".join(cells)}</row>'


def _totals_row_xml(
    layout: PlanSheetLayout,
    totals: dict[str, float],
    row_total: int | None,
    data_from: int,
) -> str:
    """
    Render the aggregating totals row (sheet row 13) - the grid's pinned summary row.

    Every additive numeric column carries a live `SUM` over the EXACT data
    range (``row_total`` rows starting at ``data_from``) - the same numbers as
    the cached sum, but as a formula so the workbook recalculates. An empty
    (0) or unknown (None) row count keeps the cached value only: there is no
    range worth summing yet, and the range must be final anyway because the
    totals row is WRITTEN BEFORE the data (rows out of `r` order are rejected
    by Excel). Columns with no sum stay empty - their number cell above spans
    both rows (see `_merged_ranges_xml`). The asset column (col_002) carries
    no aggregate either: like the grid's summary band, its number cell spans
    both rows too.
    """
    row_number = layout.header_rows + 2
    cells = []
    for index, meta in enumerate(layout.fields):
        letter = layout.letters[index + 1]
        ref = f"{letter}{row_number}"
        if meta.db_name not in totals:
            cells.append(xlsx_stream.styled_cell_xml(ref, style=1))
            continue
        cached = totals[meta.db_name]
        if row_total and not layout.values_only:
            formula = f"SUM({letter}{data_from}:{letter}{data_from + row_total - 1})"
            # A live SUM over the exact exported range, bold and bordered
            # (style 7), with the cached sum so a reader sees it at once.
            cells.append(xlsx_stream.formula_cell_xml(ref, formula, style=7, cached=cached))
        else:
            cells.append(xlsx_stream.number_cell_xml(ref, cached, style=7))
    return f'<row r="{row_number}" ht="22" customHeight="1">{"".join(cells)}</row>'


def _row_xml(
    asset_name: str,
    values: dict[str, object],
    layout: PlanSheetLayout,
    reference_labels: dict[UUID, str],
    row_number: int,
) -> str:
    """One <row> element for a prepared export row (no row-number column)."""
    letters = layout.letters
    formulas = layout.formulas
    column_by_field = layout.column_by_field
    row_text = str(row_number)
    xml = [
        f'<row r="{row_number}">',
        xlsx_stream.text_cell_xml(f"{letters[1]}{row_number}", asset_name),
    ]
    # Every kept field gets a cell when it has a value, so a reader sees the
    # full column set even when a value (often a derived field whose inputs are
    # blank) is missing. col_002 is the asset column, written above.
    for meta in layout.fields:
        if meta.db_name == _ASSET_FIELD:
            continue
        value = values.get(meta.db_name)
        if value is None:
            continue
        if meta.cell_type == "reference":
            display_value = reference_labels.get(UUID(str(value)))
            if display_value is None:
                continue
            value = display_value
        ref = f"{letters[column_by_field[meta.db_name]]}{row_number}"
        template = formulas.get(meta.db_name)
        if template is not None:
            # A calculated field keeps its Excel formula (rendered onto this
            # sheet's cell references) so the exported workbook recalculates.
            xml.append(_formula_cell_xml(ref, template.replace(_ROW_PLACEHOLDER, row_text), value, meta))
        else:
            xml.append(_data_cell_xml(ref, value, meta))
    xml.append("</row>")
    return "".join(xml)


class RenovationWorkbookWriter:
    """
    Write the XLSX incrementally: static parts and the aggregating band once.

    Rows are then appended in batches; memory stays bounded regardless of the
    plan size — used by the export-job worker and the synchronous streaming
    endpoint alike.

    ``row_total`` is the EXPECTED number of data rows, promised upfront: the
    totals row sits after the number row and BEFORE the data, because `<row>`
    elements out of `r` order are rejected by Excel (repair dialog — the old
    totals-after-data layout did exactly that), and the totals' live SUM needs
    the exact data range. A mismatch — e.g. the plan edited between the meta
    and the rows reads of a background export — raises `ExportRowCountMismatchError`
    and the caller fails fast instead of producing a broken file.
    """

    def __init__(
        self,
        sink: IO[bytes],
        layout: PlanSheetLayout,
        reference_labels: dict[UUID, str],
        totals: dict[str, float] | None = None,
        *,
        row_total: int | None,
    ) -> None:
        self.layout = layout
        self.reference_labels = reference_labels
        self.header_rows_count = len(header_rows())
        self.totals = totals or {}
        self.row_total = row_total
        # The sink is wrapped append-only (see UnseekableSink); the cast mirrors
        # the streaming queue sink: zipfile accepts any writable binary stream,
        # the static protocol just cannot express it.
        self.archive = ZipFile(
            cast("IO[bytes]", xlsx_stream.UnseekableSink(sink)), "w", compression=ZIP_DEFLATED, compresslevel=1
        )
        self.archive.writestr("[Content_Types].xml", xlsx_stream.content_types_xml())
        self.archive.writestr("_rels/.rels", xlsx_stream.ROOT_RELS_XML)
        self.archive.writestr("xl/workbook.xml", xlsx_stream.workbook_xml("Ремонт"))
        self.archive.writestr("xl/_rels/workbook.xml.rels", xlsx_stream.workbook_rels_xml())
        self.archive.writestr("xl/styles.xml", xlsx_stream.STYLES_XML)
        # The sheet entry is written APPEND-ONLY (UnseekableSink above): a seekable
        # sink would make zipfile emit a draft local header (zero CRC/sizes) that
        # it patches by seeking back at close() — after the download streamer has
        # already served those draft bytes to the browser, so the downloaded file
        # carried an invalid local header and Excel repair-dialoged it. The
        # non-seekable sink switches zipfile to data descriptors (the honest
        # "sizes follow the data" flag) — and forces plain ZIP: a streaming
        # ZIP64 entry carries an all-zero zip64 extra field in its header,
        # which real Excel rejects outright (verified via COM: a descriptor
        # entry without zip64 opens clean, with zip64 fails). The trade-off is
        # the 4 GiB uncompressed part limit — the plan sheet is ~3.5 GB and
        # stays well under it; a sheet that grows past 4 GiB would need a
        # buffered (non-tailed) write path.
        self.sheet = self.archive.open("xl/worksheets/sheet1.xml", "w")
        # The frozen pane sits below the aggregating band (11 header levels +
        # the number and totals rows), exactly like the grid's freeze; the
        # horizontal split pins the «Предприятие» column (column 1 — there is
        # no row-number column any more).
        self.sheet.write(xlsx_stream.sheet_prefix_xml(x_split=1, y_split=self.header_rows_count + 2).encode())
        self.sheet.write(layout.widths_xml.encode())
        self.sheet.write(b"<sheetData>")
        self.sheet.write(layout.header_xml.encode())
        # The aggregating band sits directly under the 11 header levels, exactly
        # like the grid: the column-number row (12), then the totals row (13)
        # written canonically BEFORE the data, then the data rows.
        self.sheet.write(_number_row_xml(layout).encode())
        self._data_from = self.header_rows_count + 3
        self.sheet.write(_totals_row_xml(layout, self.totals, row_total, self._data_from).encode())
        # Rows already emitted above the data: the 11 header levels, the
        # number row (12) and the totals row (13).
        self._above_data_rows = self.header_rows_count + 2
        # Data rows written so far. The workbook row is
        # `above + this`, NOT `above + position`: `position` is the plan's stored
        # row position, so a filtered export (surviving rows keep their original
        # positions, e.g. 232729) used to place the first data row at row 232740 -
        # thousands of blank rows after the header, which Excel shows as "header
        # only". The column A value still carries `position` (the plan's row number).
        self._written = 0

    def write_rows(
        self,
        rows: Iterable[tuple[int, str, str, dict[str, object]]],
    ) -> None:
        """Write one batch of prepared rows; call repeatedly for the whole plan."""
        for _position, asset_name, _repair_code, values in rows:
            if self.row_total is not None and self._written >= self.row_total:
                raise ExportRowCountMismatchError(_ROW_COUNT_MISMATCH_MESSAGE)
            self._written += 1
            self.sheet.write(
                _row_xml(
                    asset_name,
                    values,
                    self.layout,
                    self.reference_labels,
                    self._above_data_rows + self._written,
                ).encode()
            )

    def close(self) -> None:
        """
        Finalize the sheet and the archive.

        Raises `ExportRowCountMismatchError` when fewer rows were written than
        promised: the live SUM over the promised data range would not match
        the data actually written.
        """
        if self.row_total is not None and self._written != self.row_total:
            raise ExportRowCountMismatchError(_ROW_COUNT_MISMATCH_MESSAGE)
        self.sheet.write(b"</sheetData>")
        # Header merges plus the aggregate band's number-over-empty ranges are
        # all folded into the layout's single <mergeCells> block.
        self.sheet.write(self.layout.merged_xml.encode())
        self.sheet.write(b"</worksheet>")
        try:
            self.sheet.close()
        except RuntimeError as error:
            # zipfile's ceiling hit: the sheet part cannot be a plain entry any
            # more, and a streamed zip64 entry is not Excel-readable. Fail with
            # the user-facing wording instead of the raw RuntimeError.
            raise ExportTooLargeError(_EXPORT_TOO_LARGE_MESSAGE) from error
        self.archive.close()


def _clip_columns(
    start_column: int,
    end_column: int,
    position_by_column: dict[int, int],
) -> tuple[int, int] | None:
    """
    Map an original column span onto the exported layout, or None if dropped.

    `position_by_column` maps the original 1-based field column (work_or_repair_code → 3) to
    the exported 1-based column; a span survives only while it covers a kept
    column, and is clipped to the first and last kept column it contains.
    """
    kept = [
        position_by_column[column] for column in range(start_column, end_column + 1) if column in position_by_column
    ]
    if not kept:
        return None
    return min(kept), max(kept)


def _header_matrix(
    fields: tuple[RenovationFieldMeta, ...],
    column_by_field: dict[str, int],
) -> list[list[str | None]]:
    rows = header_rows()
    position_by_column = {_column_offset(meta): column_by_field[meta.db_name] for meta in fields}
    width = len(fields)
    matrix: list[list[str | None]] = [[None for _ in range(width)] for _ in rows]
    for _, cells in rows:
        for cell in cells:
            start_column, start_row, end_column, _ = _shifted_range(cell.excel_range)
            clipped = _clip_columns(start_column, end_column, position_by_column)
            if clipped is None:
                continue
            first_column, _ = clipped
            matrix[start_row - 1][first_column - 1] = cell.text
    return matrix


def _merged_ranges_xml(
    fields: tuple[RenovationFieldMeta, ...],
    column_by_field: dict[str, int],
    totals: dict[str, float] | None = None,
) -> str:
    """
    Fold every merged range of the plan sheet into ONE ``<mergeCells>`` block.

    The block carries the header merges plus the aggregating band's ranges;
    the shared `merge_cells_xml` drops accidental duplicates (Excel rejects
    duplicate refs with the repair dialog), and the loop below also skips the
    asset column explicitly — its band merge is named once, right before it.
    There is no corner column: the sheet starts with the «Предприятие» column.
    """
    position_by_column = {_column_offset(meta): column_by_field[meta.db_name] for meta in fields}
    range_refs: list[str] = []
    for _, cells in header_rows():
        for cell in cells:
            start_column, start_row, end_column, end_row = _shifted_range(cell.excel_range)
            clipped = _clip_columns(start_column, end_column, position_by_column)
            if clipped is None:
                continue
            first_column, last_column = clipped
            if start_row != end_row or first_column != last_column:
                range_refs.append(
                    f"{get_column_letter(first_column)}{start_row}:{get_column_letter(last_column)}{end_row}"
                )
    # The aggregate band: a column whose totals cell is empty merges its number
    # over the totals row (the grid does the same), so the strip reads as one
    # band. The header merge rows are 1..11; the band is rows 12..13.
    top = len(header_rows()) + 1
    bottom = top + 1
    for meta in fields:
        if totals and meta.db_name in totals:
            continue
        letter = get_column_letter(column_by_field[meta.db_name])
        range_refs.append(f"{letter}{top}:{letter}{bottom}")
    return xlsx_stream.merge_cells_xml(range_refs)


def _column_widths_xml(fields: tuple[RenovationFieldMeta, ...], column_by_field: dict[str, int]) -> str:
    # The «Предприятие» column (col_002) is column 1; the form fields follow.
    columns = []
    for meta in fields:
        index = column_by_field[meta.db_name]
        width = "34" if meta.db_name == _ASSET_FIELD else str(_column_width(meta.cell_type))
        columns.append(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>')
    return f"<cols>{''.join(columns)}</cols>"


def _data_cell_xml(ref: str, value: object, meta: RenovationFieldMeta) -> str:
    if meta.cell_type == "boolean":
        return xlsx_stream.text_cell_xml(ref, "Да" if value else "Нет")
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        return xlsx_stream.number_cell_xml(ref, xlsx_stream.date_serial(parsed), style=2)
    if meta.cell_type == "number":
        return xlsx_stream.number_cell_xml(ref, value, style=3 if meta.decimals else 0)
    return xlsx_stream.text_cell_xml(ref, str(value))


_FIELD_REFERENCE = re.compile(r"\b[a-z][a-z0-9_]*\b")


def _compile_formula_template(
    formula: str,
    column_by_field: dict[str, int],
    letters: dict[int, str],
) -> str | None:
    """
    Render an engine formula onto the sheet's Excel cell references ONCE.

    A field name (the semantic `snake_case` identifier frozen in migration 0041) becomes the field's
    exported column letter + the row placeholder; the row number is substituted
    per data row. Returns None when a referenced field was dropped (a block-filtered export) — the caller then falls back to
    the stored value.
    """
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        column = column_by_field.get(match.group(0))
        if column is None:
            missing = True
            return match.group(0)
        return f"{letters[column]}{_ROW_PLACEHOLDER}"

    rendered = _FIELD_REFERENCE.sub(replace, formula)
    return None if missing else rendered


def _formula_cell_xml(
    ref: str,
    formula: str,
    value: object,
    meta: RenovationFieldMeta,
) -> str:
    """Build a cell with the field's live formula (pre-escaped) plus its cached value."""
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        return xlsx_stream.formula_cell_xml(ref, formula, style=2, cached=xlsx_stream.date_serial(parsed))
    if meta.cell_type == "number":
        # Stored cols are floats; the engine may hand back an int for a calculated one.
        cached = value if isinstance(value, float) else float(str(value))
        return xlsx_stream.formula_cell_xml(ref, formula, style=3 if meta.decimals else 0, cached=cached)
    return xlsx_stream.formula_cell_xml(ref, formula)


def _shifted_range(excel_range: str) -> tuple[int, int, int, int]:
    """Return (start_column, start_row, end_column, end_row) shifted to row 1."""
    min_column, min_row, max_column, max_row = range_boundaries(excel_range)
    return min_column, min_row - HEADER_ROW_SHIFT, max_column, max_row - HEADER_ROW_SHIFT


def _column_width(cell_type: str) -> float:
    if cell_type == "boolean":
        return 8.0
    if cell_type == "date":
        return 12.0
    if cell_type == "number":
        return 16.0
    if cell_type == "reference":
        return 22.0
    return 34.0


# ─── Dictionary exports (assets, catalog options, users) ────────────────

SECTION_SHEET_NAMES: dict[str, str] = {
    "renovationAssets": "Активы",
    "renovationUsers": "Пользователи",
}

ALLOWED_KEYS: dict[str, frozenset[str]] = {
    # Computed columns («Стоимость итого», «Число ремонтных работ») are exported
    # like any other column — keep them in sync with the grid descriptors.
    "renovationAssets": frozenset(
        {"repairCode", "name", "totalCost", "repairCount", "createdAt", "updatedAt", "updatedBy"}
    ),
    "renovationUsers": frozenset(
        {
            "email",
            "fullname",
            "role",
            "block",
            "assetNames",
            "isActive",
            "lastAuthenticatedAt",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
}

# Catalog sections are dynamic: "renovationCatalog:{catalog_id}".
CATALOG_SECTION_PREFIX = "renovationCatalog:"
# The computed columns mirror «Предприятия»: the same aggregates per option.
CATALOG_ALLOWED_KEYS = frozenset({"label", "totalCost", "repairCount", "createdAt", "updatedAt", "updatedBy"})

# Dictionary columns: column key -> list name on the hidden sheet.
REFERENCE_LISTS: dict[str, str] = {
    "assetNames": "assets",
}


def validate_export_request(request: RenovationExportViewRequest) -> None:
    """Validate the request: section, limits, uniqueness, column availability, file name."""
    if request.section not in ALLOWED_KEYS and not request.section.startswith(CATALOG_SECTION_PREFIX):
        message = "Некорректная секция экспорта."
        raise ExportValidationError(message)
    validate_export_shape(request)
    allowed = (
        CATALOG_ALLOWED_KEYS if request.section.startswith(CATALOG_SECTION_PREFIX) else ALLOWED_KEYS[request.section]
    )
    validate_columns_allowed(request.columns, allowed)


def section_sheet_name(section: str) -> str:
    """Worksheet title of an export section (catalog sections use the catalog label)."""
    if section in SECTION_SHEET_NAMES:
        return SECTION_SHEET_NAMES[section]
    if section.startswith(CATALOG_SECTION_PREFIX):
        catalog_id = section.removeprefix(CATALOG_SECTION_PREFIX)
        return catalog_labels().get(catalog_id, catalog_id)
    return section


def render_workbook(
    request: RenovationExportViewRequest,
    rows: Sequence[BaseModel],
    asset_names: Sequence[str],
) -> bytes:
    """Build the dictionary Excel workbook and return its bytes (no disk write)."""
    needs_lists = any(column.key in REFERENCE_LISTS for column in request.columns)
    lists = {"assets": asset_names} if needs_lists else None
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        safe_sheet_name(section_sheet_name(request.section)),
        request.columns,
        header_pinned=request.headerPinned,
        lists=lists,
        column_lists=REFERENCE_LISTS,
        row_total=len(rows),
        values_only=request.valuesOnly,
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()
