"""
Excel export of the «Логистика» grids.

Every tab (the main «Ставки» table, the read-only dictionaries and the project
users) exports through the shared streaming renderer
(`src.common.xlsx_table`): the client sends its column shape («вся» — without
rowIds, «видимая» — with), the server re-validates the keys against the
section allowlist and writes the rows. The «Ставки» rows are scoped exactly like
the grid: an asset worker exports only their own assets' rows.
"""

from collections.abc import Sequence
from io import BytesIO
from typing import TYPE_CHECKING

from openpyxl.utils import get_column_letter

from src.common.export import (
    ExportColumnLike,
    ExportValidationError,
    safe_sheet_name,
    validate_columns_allowed,
    validate_export_shape,
)
from src.common.xlsx_table import TableWorkbookWriter

if TYPE_CHECKING:
    from pydantic import BaseModel

    from .schemas import LogisticsExportViewRequest

SECTION_SHEET_NAMES: dict[str, str] = {
    "logisticsRates": "Ставки",
    "logisticsCountries": "Страны",
    "logisticsTransportTypes": "Типы ТС и перевозки",
    "logisticsOrgs": "Активы",
    "logisticsUsers": "Пользователи",
}

# Keep in sync with the grid descriptors of the «Логистика» sections.
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "logisticsRates": frozenset(
        {
            "assetName",
            "dangerous",
            "hazardClassValue",
            "product",
            "direction",
            "countryFromName",
            "originPoint",
            "countryToName",
            "destinationPoint",
            "transportTypeName",
            "vehicleTypeName",
            "loadingTons",
            "freightRateRub",
            "freightRateUsd",
            "pprRateRub",
            "pprRateUsd",
            "deliveryRateRub",
            "deliveryRateUsd",
            "netRateRub",
            "netRateUsd",
            "priceKind",
            "note",
            # The deviation columns are computed from the previous month's net
            # rates; they are part of the grid, so the export accepts them.
            "deviationRub",
            "deviationUsd",
            "deviationPercent",
        }
    ),
    "logisticsCountries": frozenset({"shortname", "market"}),
    "logisticsTransportTypes": frozenset({"transportType", "vehicleType"}),
    "logisticsOrgs": frozenset({"prefixRu"}),
    "logisticsUsers": frozenset({"email", "fullname", "role", "assetNames", "lastAuthenticatedAt"}),
}


# The grid merges the grouping column over its run: «Страны» group by market,
# «Типы ТС» by the transport type (CellMergePlugin). The export mirrors it —
# only when that column is actually exported.
DATA_MERGE_KEYS: dict[str, tuple[str, ...]] = {
    "logisticsCountries": ("market",),
    "logisticsTransportTypes": ("transportType",),
}


def merge_rows_by(section: str, columns: Sequence[ExportColumnLike]) -> tuple[str, ...]:
    """Return the exported grouping columns whose runs the sheet merges."""
    exported = {column.key for column in columns}
    return tuple(key for key in DATA_MERGE_KEYS.get(section, ()) if key in exported)


def validate_request(request: LogisticsExportViewRequest) -> None:
    """Validate the shared shape, the section allowlist and the rates period."""
    validate_export_shape(request)
    allowed = ALLOWED_KEYS.get(request.section)
    if allowed is None:
        message = "Неизвестный раздел «Логистики» для выгрузки."
        raise ExportValidationError(message)
    validate_columns_allowed(request.columns, allowed)
    if request.section == "logisticsRates" and (request.month is None or request.year is None):
        message = "Для выгрузки ставок выберите месяц и год."
        raise ExportValidationError(message)


def _deviation_formulas(request: LogisticsExportViewRequest) -> dict[str, str]:
    """
    Live deviation formulas for the rates sheet (formula mode only).

    The previous month's net is not a column in the grid, so it is embedded as
    the constant ``net - deviation`` from the same exported row: the result is
    identical to the stored value, but the cell remains a live formula the user
    can recalculate. When the net column is not exported there is no cell to
    reference and the stored value is kept.
    """
    if request.valuesOnly or request.section != "logisticsRates":
        return {}
    formulas: dict[str, str] = {}
    for key, net_key in (
        ("deviationRub", "netRateRub"),
        ("deviationUsd", "netRateUsd"),
        ("deviationPercent", "netRateRub"),
    ):
        index = next((i for i, column in enumerate(request.columns) if column.key == net_key), None)
        if index is None:
            continue
        formulas[key] = f"{get_column_letter(index + 1)}{{row}}-({{{net_key}}}-{{{key}}})"
    return formulas


def render_workbook(request: LogisticsExportViewRequest, rows: Sequence[BaseModel]) -> bytes:
    """Build the section workbook and return its bytes (no disk write)."""
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        safe_sheet_name(SECTION_SHEET_NAMES[request.section]),
        request.columns,
        header_pinned=request.headerPinned,
        row_total=len(rows),
        merge_rows_by=merge_rows_by(request.section, request.columns),
        calculated=_deviation_formulas(request),
        values_only=request.valuesOnly,
    )
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()
