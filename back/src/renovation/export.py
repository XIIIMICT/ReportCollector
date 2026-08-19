"""
Excel export of the «Ремонт» renovation plan (plain workbook, no customer template).

The export mirrors the legacy sheet layout: a merged 11-row header built
from the form's headerCells (levels 0..10, Excel ranges B6:HI16 in the
template, shifted up so the header starts at row 1), column A as the row
number, then the 216 form fields (col_002..col_217) in columns B..HI.
Reference fields are written as display labels, numbers keep their
decimal count, dates as DD.MM.YYYY, booleans as Да/Нет.
"""

from io import BytesIO
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from src.renovation.form_schema import RenovationFieldMeta, field_metas, header_rows

if TYPE_CHECKING:
    from src.renovation.schemas import RenovationPlanRead

# The headerCells excelRange rows are the template's (6..16); the export
# starts the header at row 1, so every range shifts up by this amount.
HEADER_ROW_SHIFT = 5
NUMBER_FORMAT_DEFAULT = "General"
DATE_FORMAT = "DD.MM.YYYY"

_HEADER_FONT = Font(bold=True)
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_DATA_ALIGNMENT = Alignment(horizontal="left", vertical="center", wrap_text=True)
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def render_renovation_workbook(plan: RenovationPlanRead) -> bytes:
    """Build the renovation plan workbook and return its bytes (no disk write)."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Ремонт"
    _write_header(worksheet)
    fields = field_metas()
    for index, meta in enumerate(fields):
        worksheet.column_dimensions[get_column_letter(index + 2)].width = _column_width(meta.cell_type)
    worksheet.column_dimensions["A"].width = 6.0
    for row_index, row in enumerate(plan.rows):
        excel_row = len(header_rows()) + row_index + 1
        number_cell = worksheet.cell(row=excel_row, column=1)
        number_cell.value = row.position + 1
        number_cell.alignment = Alignment(horizontal="center", vertical="center")
        number_cell.border = _THIN_BORDER
        for index, meta in enumerate(fields):
            value = row.renovationId if meta.db_name == "col_003" else row.values.get(meta.db_name)
            _write_field_cell(worksheet, excel_row, index + 2, value, meta)
    worksheet.freeze_panes = "C12"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_header(worksheet: Worksheet) -> None:
    """Render the merged 11-row header (levels 0..10) on rows 1..11."""
    for level, cells in header_rows():
        for cell in cells:
            start_column, start_row, end_column, end_row = _shifted_range(cell.excel_range)
            if start_row != end_row or start_column != end_column:
                worksheet.merge_cells(
                    start_row=start_row,
                    start_column=start_column,
                    end_row=end_row,
                    end_column=end_column,
                )
            for row in range(start_row, end_row + 1):
                for column in range(start_column, end_column + 1):
                    styled = worksheet.cell(row=row, column=column)
                    styled.font = _HEADER_FONT
                    styled.alignment = _HEADER_ALIGNMENT
                    styled.border = _THIN_BORDER
            worksheet.cell(row=start_row, column=start_column).value = cell.text
        worksheet.row_dimensions[level + 1].height = 30.0
    worksheet.merge_cells("A1:A11")
    for row in range(1, len(header_rows()) + 1):
        number_header = worksheet.cell(row=row, column=1)
        number_header.font = _HEADER_FONT
        number_header.alignment = _HEADER_ALIGNMENT
        number_header.border = _THIN_BORDER
    worksheet["A1"] = "№"


def _shifted_range(excel_range: str) -> tuple[int, int, int, int]:
    """Return (start_column, start_row, end_column, end_row) shifted to row 1."""
    min_column, min_row, max_column, max_row = range_boundaries(excel_range)
    return min_column, min_row - HEADER_ROW_SHIFT, max_column, max_row - HEADER_ROW_SHIFT


def _write_field_cell(
    worksheet: Worksheet,
    excel_row: int,
    column: int,
    value: object,
    meta: RenovationFieldMeta,
) -> None:
    """Write one form-field value with its type's number/date format and borders."""
    cell = worksheet.cell(row=excel_row, column=column)
    cell.border = _THIN_BORDER
    cell.alignment = _DATA_ALIGNMENT
    if value is None:
        return
    if meta.cell_type == "boolean":
        cell.value = "Да" if value else "Нет"
        cell.alignment = Alignment(horizontal="center", vertical="center")
        return
    if meta.cell_type == "date":
        cell.value = value
        cell.number_format = DATE_FORMAT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        return
    if meta.cell_type == "number":
        cell.value = float(value) if isinstance(value, int | float) else value
        cell.number_format = f"0.{'0' * meta.decimals}" if meta.decimals else NUMBER_FORMAT_DEFAULT
        return
    cell.value = str(value)


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
