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
    from src.kppk.schemas import KppkExportViewRequest, KppkRealizationExportRequest

SECTION_SHEET_NAMES: dict[str, str] = {
    "kppkPositions": "Продуктовые позиции",
    "kppkRaws": "Сырьевые позиции",
    "kppkOrgs": "ДЗО",
    "kppkUsers": "Пользователи",
}

# Keep in sync with the grid descriptors of the КППК sections.
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "kppkPositions": frozenset({"productNum", "name", "orgName", "hasExcise", "createdAt", "updatedAt", "updatedBy"}),
    "kppkRaws": frozenset({"rawNum", "name", "createdAt", "updatedAt", "updatedBy"}),
    "kppkOrgs": frozenset({"code", "name", "createdAt", "updatedAt", "updatedBy"}),
    "kppkUsers": frozenset(
        {
            "email",
            "fullname",
            "isAdmin",
            "orgNames",
            "isActive",
            "lastAuthenticatedAt",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
}

# Dictionary columns: column key -> list name on the hidden sheet.
REFERENCE_LISTS: dict[str, str] = {
    # The «ДЗО» cell of a product position carries the enterprise dropdown.
    "orgName": "orgs",
}

REALIZATION_SHEET_NAME = "Реализация"
# The run that makes ONE block of a product's market rows: the name and its
# number, each merged in its own column range. Keys are the SCHEMA field names
# (`model_dump()` keys, not the export column keys — both happen to match here,
# but `productNum` is camelCase in the schema just like in the column).
REALIZATION_MERGE_BY = ("productNum", "product")
# The «Реализация» column keys per slice: the fixed product block plus the
# slice's four numeric leaves (keep in sync with the grid descriptors).
REALIZATION_FIXED_KEYS = ("productNum", "product", "region")
REALIZATION_KEYS: dict[str, frozenset[str]] = {
    "bp": frozenset(
        {*REALIZATION_FIXED_KEYS, "buyoutBpPlan", "fcaPriceBpPlan", "monthBpPlan", "fcaPriceMarketBpPlan", "exciseBp"}
    ),
    "op": frozenset(
        {
            *REALIZATION_FIXED_KEYS,
            "buyoutMaxPlan",
            "fcaPriceMaxPlan",
            "monthMaxPlan",
            "fcaPriceMarketMaxPlan",
            "exciseMaxPlan",
        }
    ),
    "fact": frozenset(
        {*REALIZATION_FIXED_KEYS, "buyoutFact", "fcaPriceFact", "fact", "fcaPriceMarketFact", "exciseFact"}
    ),
}

COST_KEYS = {
    "bp": frozenset({"productNum", "product", "fullCostBp", "productionCostBp", "variableCostBp"}),
    "op": frozenset({"productNum", "product", "fullCostOp", "productionCostOp", "variableCostOp"}),
    "fact": frozenset({"productNum", "product", "fullCostFact", "productionCostFact", "variableCostFact"}),
}


def render_cost_workbook(request: KppkRealizationExportRequest, rows: Sequence[BaseModel]) -> bytes:
    """Render the selected cost columns without product or market merges."""
    validate_export_shape(request)
    validate_columns_allowed(request.columns, COST_KEYS[request.slice])
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink, "Себестоимость", request.columns, header_pinned=request.headerPinned, row_total=len(rows)
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()


def validate_export_request(request: KppkExportViewRequest) -> None:
    """Validate the request: section, limits, uniqueness, column availability, file name."""
    if request.section not in ALLOWED_KEYS:
        message = "Некорректная секция экспорта."
        raise ExportValidationError(message)
    validate_export_shape(request)
    validate_columns_allowed(request.columns, ALLOWED_KEYS[request.section])


def validate_realization_export(request: KppkRealizationExportRequest) -> None:
    """Validate the «Реализация» export request (shape + the slice's column keys)."""
    validate_export_shape(request)
    validate_columns_allowed(request.columns, REALIZATION_KEYS[request.slice])


def render_realization_workbook(
    request: KppkRealizationExportRequest,
    rows: Sequence[BaseModel],
) -> bytes:
    """
    Build the «Реализация» workbook: the view's COLUMNS over the view's rows.

    `rows` already carry every slice leaf (the read model is one shape for all
    three slices), so the request's `columns` select what lands in the sheet —
    the same column shape the grid shows. The numeric cells keep their raw
    values; Excel formats them, like every other table export.
    """
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        safe_sheet_name(REALIZATION_SHEET_NAME),
        request.columns,
        header_pinned=request.headerPinned,
        row_total=len(rows),
        # The template folds each product's market rows into ONE block: the «№»
        # and the name are merged down over the run (the grid does the same
        # with CellMergePlugin). The product name decides the run; the number
        # is merged alongside it when the caller exports that column.
        merge_rows_by=REALIZATION_MERGE_BY,
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()


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
