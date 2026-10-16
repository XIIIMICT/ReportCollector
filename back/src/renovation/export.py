"""
Excel export of the «Ремонт» renovation plan (plain workbook, no customer template).

The export mirrors the legacy sheet layout: a merged 11-row header built
from the form's headerCells (levels 0..10, Excel ranges B6:HI16 in the
template, shifted up so the header starts at row 1), column A as the row
number, then the 216 form fields (col_002..col_217) in columns B..HI.
Reference fields are written as display labels, numbers keep their
decimal count, dates as DD.MM.YYYY, booleans as Да/Нет.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, UnsupportedOperation
from queue import Queue
from threading import Thread
from typing import IO, TYPE_CHECKING, cast
from uuid import UUID
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from src.renovation.form_schema import RenovationFieldMeta, field_metas, header_rows
from src.renovation.plan import RPlanState

if TYPE_CHECKING:
    from src.renovation.service import RenovationPlanExportData

# The headerCells excelRange rows are the template's (6..16); the export
# starts the header at row 1, so every range shifts up by this amount.
HEADER_ROW_SHIFT = 5
NUMBER_FORMAT_DEFAULT = "General"

_HEADER_FONT = Font(bold=True)
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def stream_renovation_workbook(plan: RenovationPlanExportData) -> Iterator[bytes]:
    """Yield the XLSX while it is generated so proxies receive an early byte."""
    queue: Queue[bytes | BaseException | None] = Queue(maxsize=8)

    def build() -> None:
        try:
            sink = _QueueWriter(queue)
            writer = RenovationWorkbookWriter(
                cast("IO[bytes]", sink),
                build_plan_sheet_layout(plan.block),
                plan.reference_labels,
                totals=plan.totals,
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


_ALWAYS_VISIBLE_FIELDS = frozenset({"col_003", "col_217"})
_ASSET_FIELD = "col_002"


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
    comes from the joined asset name); `col_003` and `col_217` also stay visible
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


def build_plan_sheet_layout(block: object) -> PlanSheetLayout:
    """Precompute the static sheet parts for a block-filtered export."""
    fields = _export_fields(block)
    column_by_field = {meta.db_name: 2 + index for index, meta in enumerate(fields)}
    letters = {column: get_column_letter(column) for column in range(1, len(fields) + 2)}
    formulas: dict[str, str] = {}
    for meta in fields:
        if not (meta.is_calculated and meta.formula):
            continue
        template = _compile_formula_template(meta.formula, column_by_field, letters)
        if template is not None:
            # Escaped once: the row loop only substitutes the row number.
            formulas[meta.db_name] = escape(template)
    return PlanSheetLayout(
        fields=fields,
        column_by_field=column_by_field,
        letters=letters,
        formulas=formulas,
        widths_xml=_column_widths_xml(fields),
        header_xml=_header_rows_xml(fields, column_by_field, letters),
        merged_xml=_merged_ranges_xml(fields, column_by_field),
        header_rows=len(header_rows()),
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
                xml.append(_styled_cell_xml(ref, style=1))
            else:
                xml.append(_text_cell_xml(ref, value, style=1))
        xml.append("</row>")
        parts.append("".join(xml))
    return "".join(parts)


def _aggregate_row_xml(layout: PlanSheetLayout, totals: dict[str, float]) -> str:
    """
    Render the aggregating band (sheet row 12) - the grid's number strip + totals.

    One row, mirroring the grid: every exported column carries its workbook ordinal
    shifted so the asset column («Предприятие») is 1; a numeric column with a sum
    shows the number on the first line and the sum on the second (a newline inside
    the wrapped cell), exactly like the grid's number-over-total band. Column A
    repeats the «№» corner label. A column without a sum shows its number
    alone.
    """
    row_number = layout.header_rows + 1
    cells = [_text_cell_xml(f"A{row_number}", "№", style=5)]
    for index, meta in enumerate(layout.fields):
        ref = f"{layout.letters[index + 2]}{row_number}"
        number = str(meta.ordinal - 1)
        total = totals.get(meta.db_name)
        text = f"{number}\n{_format_total(total)}" if total is not None else number
        cells.append(_text_cell_xml(ref, text, style=5))
    return f'<row r="{row_number}" ht="34" customHeight="1">{"".join(cells)}</row>'


def _format_total(value: float) -> str:
    """Format a total the way the grid shows it: whole numbers stay whole, the rest round to 2 decimals."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".replace(".", ",")


def _row_xml(
    position: int,
    asset_name: str,
    values: dict[str, object],
    layout: PlanSheetLayout,
    reference_labels: dict[UUID, str],
    row_number: int,
) -> str:
    """One <row> element for a prepared export row."""
    letters = layout.letters
    formulas = layout.formulas
    column_by_field = layout.column_by_field
    row_text = str(row_number)
    xml = [
        f'<row r="{row_number}">',
        f'<c r="{letters[1]}{row_number}"><v>{position}</v></c>',
        _text_cell_xml(f"{letters[2]}{row_number}", asset_name),
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
    Write the XLSX incrementally: static parts and the header once, rows in batches.

    Memory stays bounded regardless of the plan size — used by the export-job
    worker and the synchronous streaming endpoint alike.
    """

    def __init__(
        self,
        sink: IO[bytes],
        layout: PlanSheetLayout,
        reference_labels: dict[UUID, str],
        totals: dict[str, float] | None = None,
    ) -> None:
        self.layout = layout
        self.reference_labels = reference_labels
        self.header_rows_count = len(header_rows())
        self.archive = ZipFile(sink, "w", compression=ZIP_DEFLATED, compresslevel=1)
        self.archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        self.archive.writestr("_rels/.rels", _ROOT_RELS_XML)
        self.archive.writestr("xl/workbook.xml", _WORKBOOK_XML)
        self.archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS_XML)
        self.archive.writestr("xl/styles.xml", _STYLES_XML)
        # The sheet XML of a 300k-row plan exceeds 4 GB uncompressed: the entry
        # must opt into ZIP64 explicitly when written through a seekable sink
        # (the streaming queue sink used data descriptors and did it implicitly).
        self.sheet = self.archive.open("xl/worksheets/sheet1.xml", "w", force_zip64=True)
        self.sheet.write(_SHEET_XML_PREFIX)
        self.sheet.write(layout.widths_xml.encode())
        self.sheet.write(b"<sheetData>")
        self.sheet.write(layout.header_xml.encode())
        # The aggregating band (numbers + totals in one row) sits directly under
        # the 11 header levels, exactly like the grid: header, aggregating row,
        # then the data rows.
        self.sheet.write(_aggregate_row_xml(layout, totals or {}).encode())
        # Rows already emitted above the data: the 11 header levels + the
        # aggregating row.
        self._strip_and_totals_rows = self.header_rows_count + 1
        # Output rows written so far. The workbook row is
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
        for position, asset_name, _repair_code, values in rows:
            self._written += 1
            self.sheet.write(
                _row_xml(
                    position,
                    asset_name,
                    values,
                    self.layout,
                    self.reference_labels,
                    self._strip_and_totals_rows + self._written,
                ).encode()
            )

    def close(self) -> None:
        self.sheet.write(b"</sheetData>")
        self.sheet.write(self.layout.merged_xml.encode())
        self.sheet.write(b"</worksheet>")
        self.sheet.close()
        self.archive.close()


def _clip_columns(
    start_column: int,
    end_column: int,
    position_by_column: dict[int, int],
) -> tuple[int, int] | None:
    """
    Map an original column span onto the exported layout, or None if dropped.

    `position_by_column` maps the original 1-based field column (col_003 → 3) to
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
    width = len(fields) + 1
    matrix: list[list[str | None]] = [[None for _ in range(width)] for _ in rows]
    matrix[0][0] = "№"
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
) -> str:
    ranges = ["A1:A11"]
    position_by_column = {_column_offset(meta): column_by_field[meta.db_name] for meta in fields}
    for _, cells in header_rows():
        for cell in cells:
            start_column, start_row, end_column, end_row = _shifted_range(cell.excel_range)
            clipped = _clip_columns(start_column, end_column, position_by_column)
            if clipped is None:
                continue
            first_column, last_column = clipped
            if start_row != end_row or first_column != last_column:
                ranges.append(f"{get_column_letter(first_column)}{start_row}:{get_column_letter(last_column)}{end_row}")
    refs = "".join(f'<mergeCell ref="{cell_range}"/>' for cell_range in ranges)
    return f'<mergeCells count="{len(ranges)}">{refs}</mergeCells>'


def _column_widths_xml(fields: tuple[RenovationFieldMeta, ...]) -> str:
    # Column 1 is the synthetic row number, column 2 the asset name; the form
    # fields start at column 3.
    columns = [
        '<col min="1" max="1" width="6" customWidth="1"/>',
        '<col min="2" max="2" width="34" customWidth="1"/>',
    ]
    for index, meta in enumerate(fields, start=3):
        columns.append(f'<col min="{index}" max="{index}" width="{_column_width(meta.cell_type)}" customWidth="1"/>')
    return f"<cols>{''.join(columns)}</cols>"


def _data_cell_xml(ref: str, value: object, meta: RenovationFieldMeta) -> str:
    if meta.cell_type == "boolean":
        return _text_cell_xml(ref, "Да" if value else "Нет")
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        serial = (parsed - date(1899, 12, 30)).days
        return _number_cell_xml(ref, serial, style=2)
    if meta.cell_type == "number":
        return _number_cell_xml(ref, value, style=3 if meta.decimals else 0)
    return _text_cell_xml(ref, str(value))


_FIELD_REFERENCE = re.compile(r"col_\d+")


def _compile_formula_template(
    formula: str,
    column_by_field: dict[str, int],
    letters: dict[int, str],
) -> str | None:
    """
    Render an engine formula onto the sheet's Excel cell references ONCE.

    `col_XXX` becomes the field's exported column letter + the row placeholder;
    the row number is substituted per data row. Returns None when a referenced
    field was dropped (a block-filtered export) — the caller then falls back to
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
    """Build a cell carrying an Excel formula (pre-escaped) plus its cached value."""
    inner = f"<f>{formula}</f>"
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        serial = (parsed - date(1899, 12, 30)).days
        return f'<c r="{ref}" s="2">{inner}<v>{serial}</v></c>'
    if meta.cell_type == "number":
        style = 3 if meta.decimals else 0
        style_attr = f' s="{style}"' if style else ""
        return f'<c r="{ref}"{style_attr}>{inner}<v>{value}</v></c>'
    return f'<c r="{ref}">{inner}</c>'


def _text_cell_xml(ref: str, value: str, *, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(value)}</t></is></c>'


def _styled_cell_xml(ref: str, *, style: int = 0) -> str:
    """Build an empty styled cell, carrying the header border in merge cells."""
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}/>'


def _number_cell_xml(ref: str, value: object, *, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'


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


_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>"""
_ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""
_WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ремонт" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="0" fullCalcOnLoad="1"/></workbook>"""
_WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="DD.MM.YYYY"/><numFmt numFmtId="165" formatCode="0.00"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""
_SHEET_XML_PREFIX = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="12" topLeftCell="C13" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>"""


# ─── Dictionary exports (assets, catalog options, users) ────────────────

from collections.abc import Sequence

from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel

from src.raw_materials import export as raw_export
from src.renovation.form_schema import catalog_labels

if TYPE_CHECKING:
    from src.renovation.schemas import RenovationExportViewRequest

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
CATALOG_ALLOWED_KEYS = frozenset({"label", "createdAt", "updatedAt", "updatedBy"})

# Dictionary columns: column key -> list name on the hidden sheet.
REFERENCE_LISTS: dict[str, str] = {
    "assetNames": "assets",
}

LISTS_SHEET_NAME = "Списки"

ExportValidationError = raw_export.ExportValidationError

_ERROR_UNKNOWN_SECTION = ExportValidationError("Некорректная секция экспорта.")
_ERROR_COLUMN_COUNT = ExportValidationError("Некорректное количество экспортируемых столбцов.")
_ERROR_TOO_MANY_ROWS = ExportValidationError("Слишком много строк для экспорта.")
_ERROR_DUPLICATE_ROWS = ExportValidationError("Список экспортируемых строк содержит дубли.")
_ERROR_BAD_HEADERS = ExportValidationError("Некорректное описание заголовков экспорта.")
_ERROR_HIDDEN_COLUMN = ExportValidationError("Экспорт содержит скрытый или недоступный столбец.")
_ERROR_BAD_FILENAME = ExportValidationError("Имя файла должно заканчиваться на .xlsx и не содержать путей.")


def validate_export_request(request: RenovationExportViewRequest) -> None:
    """Validate the request: limits, uniqueness, column availability, file name."""
    if request.section not in ALLOWED_KEYS and not request.section.startswith(CATALOG_SECTION_PREFIX):
        raise _ERROR_UNKNOWN_SECTION
    if not request.columns or len(request.columns) > raw_export.MAX_EXPORT_COLUMNS:
        raise _ERROR_COLUMN_COUNT
    if request.rowIds is not None:
        if len(request.rowIds) > raw_export.MAX_EXPORT_ROWS:
            raise _ERROR_TOO_MANY_ROWS
        if len(set(request.rowIds)) != len(request.rowIds):
            raise _ERROR_DUPLICATE_ROWS
    seen: set[str] = set()
    for column in request.columns:
        if (
            not column.key.strip()
            or not column.headerPath
            or any(not part.strip() for part in column.headerPath)
            or column.key in seen
        ):
            raise _ERROR_BAD_HEADERS
        seen.add(column.key)
    allowed = (
        CATALOG_ALLOWED_KEYS if request.section.startswith(CATALOG_SECTION_PREFIX) else ALLOWED_KEYS[request.section]
    )
    if any(column.key not in allowed for column in request.columns):
        raise _ERROR_HIDDEN_COLUMN
    filename = request.fileName.strip()
    if not filename.lower().endswith(".xlsx") or any(char in filename for char in "/\\"):
        raise _ERROR_BAD_FILENAME


def _section_sheet_name(section: str) -> str:
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
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = raw_export.safe_sheet_name(_section_sheet_name(request.section))
    worksheet.freeze_panes = None
    _write_headers(worksheet, request)
    if request.headerPinned:
        worksheet.freeze_panes = "A2"
    for index, column in enumerate(request.columns):
        worksheet.column_dimensions[get_column_letter(index + 1)].width = min(60.0, max(8.0, column.width))
    if any(column.key in REFERENCE_LISTS for column in request.columns):
        reference_sheet, reference_count = _reference_sheet(workbook, asset_names)
        _apply_reference_validation(worksheet, reference_sheet, reference_count, request, len(rows))
    for row_index, row in enumerate(rows):
        excel_row = row_index + 2
        values = row.model_dump()
        for column_index, column in enumerate(request.columns):
            _write_cell(worksheet, excel_row, column_index + 1, values.get(column.key), column.numberFormat)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_headers(worksheet: Worksheet, request: RenovationExportViewRequest) -> None:
    for index, column in enumerate(request.columns):
        cell = worksheet.cell(row=1, column=index + 1)
        cell.value = column.headerPath[-1]
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGNMENT
        cell.border = _THIN_BORDER
    worksheet.row_dimensions[1].height = 34.0


def _write_cell(worksheet: Worksheet, row: int, column: int, value: object, number_format: str | None) -> None:
    cell = worksheet.cell(row=row, column=column)
    if isinstance(value, datetime):
        cell.value = value.replace(tzinfo=None) if value.tzinfo is not None else value
        cell.number_format = raw_export.DATETIME_FORMAT
    elif isinstance(value, bool):
        cell.value = value
    elif isinstance(value, int | float):
        cell.value = value
        cell.number_format = number_format or NUMBER_FORMAT_DEFAULT
    elif isinstance(value, list | tuple):
        cell.value = _truncate(", ".join(str(item) for item in value if item is not None))
    elif value is not None:
        cell.value = _truncate(str(value))
    cell.border = _THIN_BORDER


def _reference_sheet(workbook: Workbook, asset_names: Sequence[str]) -> tuple[Worksheet, int]:
    sheet = workbook.create_sheet(LISTS_SHEET_NAME)
    sheet.sheet_state = "hidden"
    for row_index, item in enumerate(asset_names, start=1):
        sheet.cell(row=row_index, column=1).value = item
    return sheet, len(asset_names)


def _apply_reference_validation(
    worksheet: Worksheet,
    reference_sheet: Worksheet,
    item_count: int,
    request: RenovationExportViewRequest,
    row_count: int,
) -> None:
    if row_count == 0 or item_count == 0:
        return
    lists_sheet_name = reference_sheet.title
    for column_index, column in enumerate(request.columns):
        if column.key not in REFERENCE_LISTS:
            continue
        validation = DataValidation(
            type="list",
            formula1=f"={lists_sheet_name}!$A$1:$A${item_count}",
            allow_blank=True,
        )
        worksheet.add_data_validation(validation)
        data_letter = get_column_letter(column_index + 1)
        validation.add(f"{data_letter}2:{data_letter}{row_count + 1}")


def _truncate(value: str) -> str:
    if len(value) > raw_export.MAX_CELL_CHARS:
        return value[: raw_export.MAX_CELL_CHARS]
    return value
