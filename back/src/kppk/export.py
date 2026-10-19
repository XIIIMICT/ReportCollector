"""
Excel export of the «КППК» dictionaries of the source-copy phase.

The three UI dictionaries (products, raws, ДЗО) export as flat tables through
the shared streaming renderer (`src.common.xlsx_table`): the client sends its
column shape («вся» — without rowIds, «видимая» — with), the server
re-validates the keys on the section allowlist and writes the rows.
The «ДЗО» column of «Продуктовые позиции» gets a dropdown list from the
enterprise dictionary on a hidden sheet — the same «Сырьё»/«Ремонт»
vocabulary.
"""

from collections.abc import Sequence
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.common.export import (
    ExportValidationError,
    safe_sheet_name,
    validate_columns_allowed,
    validate_export_shape,
)
from src.common.xlsx_table import TableWorkbookWriter

if TYPE_CHECKING:
    from src.kppk.schemas import KppkExportViewRequest

SECTION_SHEET_NAMES: dict[str, str] = {
    "kppkPositions": "Продуктовые позиции",
    "kppkRaws": "Сырьевые позиции",
    "kppkOrgs": "ДЗО",
}

# Keep in sync with the grid descriptors of the КППК sections.
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "kppkPositions": frozenset({"productNum", "name", "orgNames", "createdAt", "updatedAt", "updatedBy"}),
    "kppkRaws": frozenset({"rawNum", "name", "createdAt", "updatedAt", "updatedBy"}),
    "kppkOrgs": frozenset({"code", "name", "createdAt", "updatedAt", "updatedBy"}),
}

# Dictionary columns: column key -> list name on the hidden sheet.
REFERENCE_LISTS: dict[str, str] = {
    "orgNames": "orgs",
}


def validate_export_request(request: KppkExportViewRequest) -> None:
    """Validate the request: section, limits, uniqueness, column availability, file name."""
    if request.section not in ALLOWED_KEYS:
        message = "Некорректная секция экспорта."
        raise ExportValidationError(message)
    validate_export_shape(request)
    validate_columns_allowed(request.columns, ALLOWED_KEYS[request.section])


def render_workbook(
    request: KppkExportViewRequest,
    rows: Sequence[BaseModel],
    org_names: Sequence[str],
) -> bytes:
    """Build the dictionary Excel workbook and return its bytes (no disk write)."""
    needs_lists = any(column.key in REFERENCE_LISTS for column in request.columns)
    lists = {"orgs": org_names} if needs_lists else None
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        safe_sheet_name(SECTION_SHEET_NAMES[request.section]),
        request.columns,
        header_pinned=request.headerPinned,
        lists=lists,
        column_lists=REFERENCE_LISTS,
        row_total=len(rows),
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()
