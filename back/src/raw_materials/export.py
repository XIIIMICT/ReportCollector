"""
Excel export of the «Сырьё» project tables.

Renders through the shared streaming renderer (`src.common.xlsx_table`:
«Сырьё», «Ремонт» and КППК tables are the same workbook shape) — this module
keeps only the project vocabulary: section names, allowed keys, calculated
operands, reference lists. Calculated columns are written as formulas,
dictionary fields get dropdown lists through a hidden sheet, numbers and
dates in their own formats.
"""

from collections.abc import Sequence
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.common.export import (
    ERROR_HIDDEN_COLUMN,
    ExportValidationError,
    safe_sheet_name,
    validate_columns_allowed,
    validate_export_shape,
)
from src.common.xlsx_table import TableWorkbookWriter

if TYPE_CHECKING:
    from src.raw_materials.schemas import ExportViewRequest, SectionId

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
            "storageStandardTons",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
    "rawMaterials": frozenset({"name", "assetNames", "createdAt", "updatedAt", "updatedBy"}),
    "assets": frozenset({"name", "createdAt", "updatedAt", "updatedBy"}),
    "deliveryBases": frozenset({"name", "createdAt", "updatedAt", "updatedBy"}),
    "users": frozenset(
        {
            "email",
            "fullname",
            "isAdmin",
            "assetNames",
            "isActive",
            "lastAuthenticatedAt",
            "createdAt",
            "updatedAt",
            "updatedBy",
        }
    ),
}

CALCULATED_OPERANDS: dict[str, tuple[str, str]] = {
    "demandDeviationTons": ("demandPlanTons", "consumptionFactTons"),
    "deliveryDeviationTons": ("deliveryPlanTons", "deliveryFactTons"),
}

# Dictionary columns: column key -> list name on the hidden sheet.
REFERENCE_LISTS: dict[str, str] = {
    "deliveryBasisName": "deliveryBases",
    "assetNames": "assets",
}


def validate_export_request(request: ExportViewRequest, show_asset_column: bool) -> None:
    """Validate the request: limits, uniqueness, column availability, file name."""
    validate_export_shape(request)
    validate_columns_allowed(request.columns, ALLOWED_KEYS[request.section])
    if not show_asset_column and any(column.key == "assetName" for column in request.columns):
        raise ExportValidationError(ERROR_HIDDEN_COLUMN)


def render_workbook(
    request: ExportViewRequest,
    rows: Sequence[BaseModel],
    asset_names: Sequence[str],
    delivery_bases: Sequence[str],
) -> bytes:
    """Build the Excel workbook and return its bytes (no disk write)."""
    needs_lists = any(column.key in REFERENCE_LISTS for column in request.columns)
    lists = {"assets": asset_names, "deliveryBases": delivery_bases} if needs_lists else None
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        safe_sheet_name(SECTION_SHEET_NAMES[request.section]),
        request.columns,
        header_pinned=request.headerPinned,
        lists=lists,
        column_lists=REFERENCE_LISTS,
        calculated=CALCULATED_OPERANDS,
        row_total=len(rows),
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()
