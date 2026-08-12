"""
Выгрузка таблиц проекта «Сырьё» в Excel.

Повторяет поведение экспорта Tauri-приложения (ReportCollector-RawMaterials),
но без цветовых заливок: расчётные столбцы пишутся формулами, справочные поля
получают выпадающие списки через скрытый лист, числа и даты — в своих форматах.
"""

from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import BaseModel

if TYPE_CHECKING:
    from src.raw_materials.schemas import ExportViewRequest, SectionId

MAX_EXPORT_ROWS = 100_000
MAX_EXPORT_COLUMNS = 64
MAX_CELL_CHARS = 32_767

SECTION_SHEET_NAMES: dict[SectionId, str] = {
    "materialMovement": "Движение сырья",
    "rawMaterialPrices": "Цены",
    "rawMaterials": "Сырьевые позиции",
    "assets": "Активы",
    "deliveryBases": "Базисы поставки",
    "users": "Пользователи",
}

ALLOWED_KEYS: dict[SectionId, frozenset[str]] = {
    "materialMovement": frozenset(
        {
            "rawMaterialName",
            "assetName",
            "businessPlanTons",
            "demandPlanTons",
            "consumptionFactTons",
            "demandDeviationTons",
            "deliveryPlanTons",
            "deliveryFactTons",
            "deliveryDeviationTons",
            "balanceStartTons",
            "balanceOnSiteTons",
            "balanceInTransitTons",
            "balanceEndTons",
            "storageStandardTons",
            "deviationReason",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
    "rawMaterialPrices": frozenset(
        {
            "rawMaterialName",
            "deliveryBasisName",
            "assetName",
            "fact2025",
            "businessPlanQuantityTons",
            "businessPlanUsdPerTon",
            "businessPlanRubPerTon",
            "deliveryCostRub",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
    "rawMaterials": frozenset({"name", "assetNames", "createdAt", "updatedAt", "updatedBy"}),
    "assets": frozenset({"name", "createdAt", "updatedAt", "updatedBy"}),
    "deliveryBases": frozenset({"name", "createdAt", "updatedAt", "updatedBy"}),
    "users": frozenset(
        {"email", "fullname", "isAdmin", "assetNames", "isActive", "createdAt", "updatedAt", "updatedBy"}
    ),
}

CALCULATED_OPERANDS: dict[str, tuple[str, str]] = {
    "demandDeviationTons": ("demandPlanTons", "consumptionFactTons"),
    "deliveryDeviationTons": ("deliveryPlanTons", "deliveryFactTons"),
}

# Справочные столбцы: ключ колонки -> имя списка на скрытом листе.
REFERENCE_LISTS: dict[str, str] = {
    "deliveryBasisName": "deliveryBases",
    "assetNames": "assets",
}

NUMBER_FORMAT_DEFAULT = "General"
DATETIME_FORMAT = "DD.MM.YYYY HH:MM:SS"
LISTS_SHEET_NAME = "Списки"

_HEADER_FONT = Font(bold=True)
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


class ExportValidationError(ValueError):
    """Некорректный запрос на выгрузку таблицы в Excel."""


_ERROR_COLUMN_COUNT = "Некорректное количество экспортируемых столбцов."
_ERROR_TOO_MANY_ROWS = "Слишком много строк для экспорта."
_ERROR_DUPLICATE_ROWS = "Список экспортируемых строк содержит дубли."
_ERROR_BAD_HEADERS = "Некорректное описание заголовков экспорта."
_ERROR_HIDDEN_COLUMN = "Экспорт содержит скрытый или недоступный столбец."
_ERROR_BAD_FILENAME = "Имя файла должно заканчиваться на .xlsx и не содержать пути."


def validate_export_request(request: ExportViewRequest, show_asset_column: bool) -> None:
    """Проверяет запрос: лимиты, уникальность, доступность столбцов, имя файла."""
    if not request.columns or len(request.columns) > MAX_EXPORT_COLUMNS:
        raise ExportValidationError(_ERROR_COLUMN_COUNT)
    if request.rowIds is not None:
        if len(request.rowIds) > MAX_EXPORT_ROWS:
            raise ExportValidationError(_ERROR_TOO_MANY_ROWS)
        if len(set(request.rowIds)) != len(request.rowIds):
            raise ExportValidationError(_ERROR_DUPLICATE_ROWS)
    seen: set[str] = set()
    for column in request.columns:
        if (
            not column.key.strip()
            or not column.headerPath
            or any(not part.strip() for part in column.headerPath)
            or column.key in seen
        ):
            raise ExportValidationError(_ERROR_BAD_HEADERS)
        seen.add(column.key)
    allowed = ALLOWED_KEYS[request.section]
    if any(column.key not in allowed for column in request.columns):
        raise ExportValidationError(_ERROR_HIDDEN_COLUMN)
    if not show_asset_column and any(column.key == "assetName" for column in request.columns):
        raise ExportValidationError(_ERROR_HIDDEN_COLUMN)
    filename = request.fileName.strip()
    if not filename.lower().endswith(".xlsx") or any(char in filename for char in "/\\"):
        raise ExportValidationError(_ERROR_BAD_FILENAME)


def render_workbook(
    request: ExportViewRequest,
    rows: Sequence[BaseModel],
    asset_names: Sequence[str],
    delivery_bases: Sequence[str],
) -> bytes:
    """Собирает книгу Excel и возвращает её байты (без записи на диск)."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = safe_sheet_name(SECTION_SHEET_NAMES[request.section])
    worksheet.freeze_panes = None
    header_depth = max(len(column.headerPath) for column in request.columns)
    _write_headers(worksheet, request, header_depth)
    if request.headerPinned:
        worksheet.freeze_panes = f"A{header_depth + 1}"
    for index, column in enumerate(request.columns):
        worksheet.column_dimensions[get_column_letter(index + 1)].width = min(60.0, max(8.0, column.width))
    if any(column.key in REFERENCE_LISTS for column in request.columns):
        reference_sheet, reference_counts = _reference_sheet(workbook, asset_names, delivery_bases)
        _apply_reference_validation(worksheet, reference_sheet, reference_counts, request, header_depth, len(rows))
    column_letters = {column.key: get_column_letter(index + 1) for index, column in enumerate(request.columns)}
    for row_index, row in enumerate(rows):
        excel_row = header_depth + row_index + 1
        values = row.model_dump()
        for column_index, column in enumerate(request.columns):
            _write_cell(
                worksheet,
                excel_row,
                column_index + 1,
                values.get(column.key),
                column.numberFormat,
                column.key,
                column_letters,
            )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_headers(worksheet: Worksheet, request: ExportViewRequest, depth: int) -> None:
    for level in range(depth):
        start = 0
        while start < len(request.columns):
            path = request.columns[start].headerPath
            if level >= len(path):
                start += 1
                continue
            end = start
            while (
                end + 1 < len(request.columns)
                and level < len(request.columns[end + 1].headerPath)
                and request.columns[end + 1].headerPath[: level + 1] == path[: level + 1]
            ):
                end += 1
            label = path[level]
            leaf = level + 1 == len(path)
            end_row = depth - 1 if leaf else level
            _style_range(worksheet, level, start, end_row, end)
            if start == end and end_row == level:
                worksheet.cell(row=level + 1, column=start + 1).value = label
            else:
                worksheet.merge_cells(
                    start_row=level + 1,
                    start_column=start + 1,
                    end_row=end_row + 1,
                    end_column=end + 1,
                )
                worksheet.cell(row=level + 1, column=start + 1).value = label
            start = end + 1
    for row in range(1, depth + 1):
        worksheet.row_dimensions[row].height = 34.0


def _style_range(
    worksheet: Worksheet,
    start_row: int,
    start_column: int,
    end_row: int,
    end_column: int,
) -> None:
    for row in range(start_row + 1, end_row + 2):
        for column in range(start_column + 1, end_column + 2):
            cell = worksheet.cell(row=row, column=column)
            cell.font = _HEADER_FONT
            cell.alignment = _HEADER_ALIGNMENT
            cell.border = _THIN_BORDER


def _write_cell(
    worksheet: Worksheet,
    row: int,
    column: int,
    value: object,
    number_format: str | None,
    column_key: str,
    column_letters: dict[str, str],
) -> None:
    operands = CALCULATED_OPERANDS.get(column_key)
    if operands is not None:
        formula = _calculated_formula(column_key, row, column_letters)
        if formula is not None:
            cell = worksheet.cell(row=row, column=column)
            cell.value = formula
            cell.number_format = number_format or NUMBER_FORMAT_DEFAULT
            cell.border = _THIN_BORDER
            return
    cell = worksheet.cell(row=row, column=column)
    if isinstance(value, datetime):
        cell.value = value.replace(tzinfo=None) if value.tzinfo is not None else value
        cell.number_format = DATETIME_FORMAT
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


def _calculated_formula(key: str, excel_row: int, column_letters: dict[str, str]) -> str | None:
    operands = CALCULATED_OPERANDS[key]
    letters = [column_letters.get(operand) for operand in operands]
    if any(letter is None for letter in letters):
        return None
    return f"={letters[0]}{excel_row}-{letters[1]}{excel_row}"


def _reference_sheet(
    workbook: Workbook,
    asset_names: Sequence[str],
    delivery_bases: Sequence[str],
) -> tuple[Worksheet, dict[str, int]]:
    sheet = workbook.create_sheet(LISTS_SHEET_NAME)
    sheet.sheet_state = "hidden"
    lists: dict[str, Sequence[str]] = {
        "assets": asset_names,
        "deliveryBases": delivery_bases,
    }
    counts: dict[str, int] = {}
    for column_index, name in enumerate(["assets", "deliveryBases"], start=1):
        items = lists[name]
        counts[name] = len(items)
        for row_index, item in enumerate(items, start=1):
            sheet.cell(row=row_index, column=column_index).value = item
    return sheet, counts


def _apply_reference_validation(
    worksheet: Worksheet,
    reference_sheet: Worksheet,
    reference_counts: dict[str, int],
    request: ExportViewRequest,
    header_depth: int,
    row_count: int,
) -> None:
    if row_count == 0:
        return
    lists_sheet_name = reference_sheet.title
    for column_index, column in enumerate(request.columns):
        list_name = REFERENCE_LISTS.get(column.key)
        if list_name is None:
            continue
        item_count = reference_counts[list_name]
        if item_count == 0:
            continue
        list_column = 1 if list_name == "assets" else 2
        letter = get_column_letter(list_column)
        validation = DataValidation(
            type="list",
            formula1=f"={lists_sheet_name}!${letter}$1:${letter}${item_count}",
            allow_blank=True,
        )
        worksheet.add_data_validation(validation)
        data_letter = get_column_letter(column_index + 1)
        validation.add(f"{data_letter}{header_depth + 1}:{data_letter}{header_depth + row_count}")


def safe_sheet_name(value: str) -> str:
    trimmed = value.strip()
    if not trimmed or len(trimmed) > 31 or any(char in trimmed for char in "[]:*?/\\"):
        return "Отчёт"
    return trimmed


def _truncate(value: str) -> str:
    if len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS]
    return value
