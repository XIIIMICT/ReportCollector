"""
Static form schema of the renovation plan (from renovation-form.json).

The legacy form defines 216 fields (col_002..col_217): cell types, reference
catalogs, calculated flags and formulas. The app resource lives in
form_data/renovation-form.json (a copy of the legacy artifact); the compiled
FormulaEngine and the editable-field set are built once at import time.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.renovation.calculations import FormulaDefinition, FormulaEngine

FORM_DATA_PATH = Path(__file__).parent / "form_data" / "renovation-form.json"


@dataclass(frozen=True)
class RenovationFieldMeta:
    """A single renovation form field (from the legacy form description)."""

    db_name: str
    label: str
    cell_type: str
    catalog: str | None
    is_calculated: bool
    is_read_only: bool
    decimals: int | None
    position: int
    ordinal: int
    responsibility_block: str | None
    # The engine formula (col_XXX references) and the legacy Excel formula —
    # the export renders the former onto the sheet's cell references.
    formula: str | None = None
    excel_formula: str | None = None


@dataclass(frozen=True)
class RenovationHeaderCell:
    """A merged header cell of the legacy form (11 levels, Excel ranges)."""

    cell_id: str
    text: str
    start_position: int
    col_span: int
    row_span: int
    level: int
    excel_range: str


@lru_cache(maxsize=1)
def _load_form() -> dict:
    return json.loads(FORM_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def field_metas() -> tuple[RenovationFieldMeta, ...]:
    """All form fields in the form's own order (by position)."""
    metas = [
        RenovationFieldMeta(
            db_name=field["dbName"],
            label=field["label"],
            cell_type=field["cellType"],
            catalog=field.get("catalog"),
            is_calculated=bool(field["isCalculated"]),
            is_read_only=bool(field.get("isReadOnly")),
            decimals=field.get("decimals"),
            position=int(field["position"]),
            ordinal=int(field["ordinal"]),
            responsibility_block=field.get("responsibilityBlock"),
            formula=field.get("formula"),
            excel_formula=field.get("excelFormula"),
        )
        for field in _load_form()["fields"]
    ]
    return tuple(sorted(metas, key=lambda meta: meta.position))


@lru_cache(maxsize=1)
def _by_name() -> dict[str, RenovationFieldMeta]:
    return {meta.db_name: meta for meta in field_metas()}


def field_meta(db_name: str) -> RenovationFieldMeta | None:
    return _by_name().get(db_name)


@lru_cache(maxsize=1)
def editable_fields() -> tuple[str, ...]:
    """Writable fields: neither calculated nor read-only (col_003 stays read-only)."""
    return tuple(meta.db_name for meta in field_metas() if not meta.is_calculated and not meta.is_read_only)


@lru_cache(maxsize=1)
def formula_engine() -> FormulaEngine:
    """Compile the formula engine from the legacy formulas (single instance)."""
    definitions = [
        FormulaDefinition(
            field=field["dbName"],
            cell_type=field["cellType"],
            formula=field.get("formula"),
            is_calculated=field["isCalculated"],
            decimal_places=field.get("decimals"),
        )
        for field in _load_form()["fields"]
    ]
    return FormulaEngine(definitions)


@lru_cache(maxsize=1)
def catalog_labels() -> dict[str, str]:
    """Return reference catalog display names (e.g. «Группа критичности оборудования»)."""
    return {catalog["id"]: catalog["label"] for catalog in _load_form()["catalogs"]}


@lru_cache(maxsize=1)
def header_rows() -> tuple[tuple[int, tuple[RenovationHeaderCell, ...]], ...]:
    """
    Return the merged header layout by level (0..10) in the template's Excel ranges.

    Level 0 is the responsibility-block row; deeper levels split the columns
    down to single fields. The field column of a cell is
    start_position + 2 (the 0-based index counts from column B; column A is
    the synthetic row-number column).
    """
    form = _load_form()
    by_id = {cell["id"]: cell for cell in form["headerCells"]}
    rows: list[tuple[int, tuple[RenovationHeaderCell, ...]]] = []
    for entry in form["headerRows"]:
        cells = tuple(
            RenovationHeaderCell(
                cell_id=cell["id"],
                text=cell.get("text", ""),
                start_position=int(cell["startPosition"]),
                col_span=int(cell["colSpan"]),
                row_span=int(cell["rowSpan"]),
                level=int(cell["level"]),
                excel_range=cell["excelRange"],
            )
            for cell_id in entry["cellIds"]
            if (cell := by_id.get(cell_id)) is not None
        )
        rows.append((int(entry["level"]), cells))
    return tuple(rows)
