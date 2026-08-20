"""
Excel export of the «Ремонт» renovation plan (plain workbook, no customer template).

The export mirrors the legacy sheet layout: a merged 11-row header built
from the form's headerCells (levels 0..10, Excel ranges B6:HI16 in the
template, shifted up so the header starts at row 1), column A as the row
number, then the 216 form fields (col_002..col_217) in columns B..HI.
Reference fields are written as display labels, numbers keep their
decimal count, dates as DD.MM.YYYY, booleans as Да/Нет.
"""

from datetime import datetime
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
    "renovationAssets": frozenset({"repairCode", "name", "createdAt", "updatedAt", "updatedBy"}),
    "renovationUsers": frozenset(
        {"email", "fullname", "isAdmin", "assetNames", "isActive", "createdAt", "updatedAt", "updatedBy"}
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
