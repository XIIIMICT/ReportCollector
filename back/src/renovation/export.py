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
from collections.abc import Iterator
from datetime import date, datetime
from io import BytesIO, UnsupportedOperation
from queue import Queue
from threading import Thread
from typing import IO, TYPE_CHECKING
from uuid import UUID
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from src.renovation.form_schema import RenovationFieldMeta, field_metas, header_rows

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
            with ZipFile(sink, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:  # ty: ignore[no-matching-overload]
                archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
                archive.writestr("_rels/.rels", _ROOT_RELS_XML)
                archive.writestr("xl/workbook.xml", _WORKBOOK_XML)
                archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS_XML)
                archive.writestr("xl/styles.xml", _STYLES_XML)
                with archive.open("xl/worksheets/sheet1.xml", "w") as worksheet:
                    _write_plan_sheet_xml(worksheet, plan)
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


def _write_plan_sheet_xml(worksheet: IO[bytes], plan: RenovationPlanExportData) -> None:
    """Write sheet1.xml incrementally so memory stays bounded for 300k rows."""
    fields = _export_fields(plan.block)
    column_by_field = {meta.db_name: 2 + index for index, meta in enumerate(fields)}
    worksheet.write(_SHEET_XML_PREFIX)
    worksheet.write(_column_widths_xml(fields).encode())
    worksheet.write(b"<sheetData>")
    for row_number, values in enumerate(_header_matrix(fields, column_by_field), start=1):
        xml = [f'<row r="{row_number}" ht="30" customHeight="1">']
        for column, value in enumerate(values, start=1):
            # A merged header range is expressed by a top-left <c> plus the
            # <mergeCells> entry; every other cell of the range still needs the
            # bordered style, or Excel draws the frame only around the top-left
            # cell and the grid looks underdrawn down and right.
            if value is None:
                xml.append(_styled_cell_xml(row_number, column, style=1))
            else:
                xml.append(_text_cell_xml(row_number, column, value, style=1))
        xml.append("</row>")
        worksheet.write("".join(xml).encode())
    for position, asset_name, _, row_values in plan.rows:
        row_number = len(header_rows()) + position
        xml = [
            f'<row r="{row_number}">',
            _number_cell_xml(row_number, 1, position),
            _text_cell_xml(row_number, 2, asset_name),
        ]
        # Every kept field gets a cell — empty ones included — so a reader sees
        # the full column set even when a value (often a derived field whose
        # inputs are blank) is missing. col_002 is the asset column, written above.
        for meta in fields:
            if meta.db_name == _ASSET_FIELD:
                continue
            value = row_values.get(meta.db_name)
            if value is None:
                continue
            if meta.cell_type == "reference":
                display_value = plan.reference_labels.get(UUID(str(value)))
                if display_value is None:
                    continue
                value = display_value
            column = column_by_field[meta.db_name]
            # A calculated field keeps its Excel formula (rendered onto this
            # sheet's cell references) so the exported workbook recalculates.
            formula = (
                _render_excel_formula(meta.formula, column_by_field, row_number)
                if meta.is_calculated and meta.formula
                else None
            )
            if formula is not None:
                xml.append(_formula_cell_xml(row_number, column, formula, value, meta))
            else:
                xml.append(_data_cell_xml(row_number, column, value, meta))
        xml.append("</row>")
        worksheet.write("".join(xml).encode())
    worksheet.write(b"</sheetData>")
    worksheet.write(_merged_ranges_xml(fields, column_by_field).encode())
    worksheet.write(b"</worksheet>")


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


def _data_cell_xml(row: int, column: int, value: object, meta: RenovationFieldMeta) -> str:
    if meta.cell_type == "boolean":
        return _text_cell_xml(row, column, "Да" if value else "Нет")
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        serial = (parsed - date(1899, 12, 30)).days
        return _number_cell_xml(row, column, serial, style=2)
    if meta.cell_type == "number":
        return _number_cell_xml(row, column, value, style=3 if meta.decimals else 0)
    return _text_cell_xml(row, column, str(value))


_FIELD_REFERENCE = re.compile(r"col_\d+")


def _render_excel_formula(formula: str, column_by_field: dict[str, int], row: int) -> str | None:
    """
    Render an engine formula onto the sheet's Excel cell references.

    `col_XXX` becomes the field's exported column letter + the data row. Returns
    None when a referenced field was dropped (a block-filtered export) — the
    caller then falls back to the stored value.
    """
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        column = column_by_field.get(match.group(0))
        if column is None:
            missing = True
            return match.group(0)
        return f"{get_column_letter(column)}{row}"

    rendered = _FIELD_REFERENCE.sub(replace, formula)
    return None if missing else rendered


def _formula_cell_xml(
    row: int,
    column: int,
    formula: str,
    value: object,
    meta: RenovationFieldMeta,
) -> str:
    """Build a cell carrying an Excel formula plus its cached value."""
    ref = f"{get_column_letter(column)}{row}"
    inner = f"<f>{escape(formula)}</f>"
    if meta.cell_type == "date":
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        serial = (parsed - date(1899, 12, 30)).days
        return f'<c r="{ref}" s="2">{inner}<v>{serial}</v></c>'
    if meta.cell_type == "number":
        style = 3 if meta.decimals else 0
        style_attr = f' s="{style}"' if style else ""
        return f'<c r="{ref}"{style_attr}>{inner}<v>{value}</v></c>'
    return f'<c r="{ref}">{inner}</c>'


def _text_cell_xml(row: int, column: int, value: str, *, style: int = 0) -> str:
    ref = f"{get_column_letter(column)}{row}"
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(value)}</t></is></c>'


def _styled_cell_xml(row: int, column: int, *, style: int = 0) -> str:
    """Build an empty styled cell, carrying the header border in merge cells."""
    ref = f"{get_column_letter(column)}{row}"
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}/>'


def _number_cell_xml(row: int, column: int, value: object, *, style: int = 0) -> str:
    ref = f"{get_column_letter(column)}{row}"
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
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ремонт" sheetId="1" r:id="rId1"/></sheets></workbook>"""
_WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="DD.MM.YYYY"/><numFmt numFmtId="165" formatCode="0.00"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""
_SHEET_XML_PREFIX = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="11" topLeftCell="C12" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>"""


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
