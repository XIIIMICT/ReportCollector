"""
Static form schema of the renovation plan (from renovation-form.json).

The form defines 217 fields (col_002..col_218): cell types, reference
catalogs, calculated flags and formulas. The app resource lives in
form_data/renovation-form.json (a copy of the legacy artifact); the compiled
FormulaEngine and the editable-field set are built once at import time.
The retired «Корректировка» (the old col_218 at position 23) is gone; the db
name now belongs to «Номер сметы», the first column of the КСиР block.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.renovation.calculations import FormulaDefinition, FormulaEngine

FORM_DATA_PATH = Path(__file__).parent / "form_data" / "renovation-form.json"

# No hidden fields anymore. The retired «Корректировка» (the old col_218,
# position 23) is gone from the JSON; its db name now belongs to the new
# «Номер сметы» field — the first column of the «Дирекция КСиР» block. With the
# gap removed, positions/ordinals are continuous (1..217 / 2..218), so the
# displayed column numbers stay continuous too.
HIDDEN_FIELDS = frozenset()

# Fields whose legacy «calculated» / «read-only» flags the app overrides.
#
# col_003 (the renovation ID) used to be a read-only value the generator derived
# from its components (`renovation_id.calculate_renovation_id`). The formula is
# OFF for now: the agreement processes and the state of the data need the field
# to be a plain typed value, loaded from the template as-is. The generator stays
# in the codebase (`renovation_id.py`, the legacy JSON entry with its formula
# metadata) so it can be switched back on by removing the entry from this map —
# nothing is deleted.
CALCULATED_OVERRIDES: dict[str, bool] = {"col_003": False}
READ_ONLY_OVERRIDES: dict[str, bool] = {"col_003": False}


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
    # Validation rules from the source workbook (kind + parameters). Used by the
    # edit path (rejects input violating a rule) and by the grid to highlight a
    # calculated cell whose value breaks its rule (e.g. a negative duration).
    rules: tuple[dict[str, object], ...] = ()
    # Reference label used for a `field_compare` rule (e.g. «Дата окончания» ≥
    # «Дата начала»): the compared field's label, resolved from the form.
    allow_null: bool = True


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
    form = json.loads(FORM_DATA_PATH.read_text(encoding="utf-8"))
    # Workbook labels may contain trailing CR/LF, non-breaking spaces or repeated
    # spaces. Normalize display metadata only, never user-entered plan values.
    for cell in form["headerCells"]:
        cell["text"] = normalize_label(cell.get("text", ""))
    for field in form["fields"]:
        field["label"] = normalize_label(field["label"])
        field["categories"] = [normalize_label(part) for part in field.get("categories", [])]
    for catalog in form["catalogs"]:
        catalog["label"] = normalize_label(catalog["label"])
    return form


def normalize_label(value: str) -> str:
    """Collapse whitespace in a form label to single ordinary spaces."""
    return " ".join(value.split())


@lru_cache(maxsize=1)
def field_metas() -> tuple[RenovationFieldMeta, ...]:
    """All form fields in the form's own order (by position)."""
    metas = [
        RenovationFieldMeta(
            db_name=field["dbName"],
            label=field["label"],
            cell_type=field["cellType"],
            catalog=field.get("catalog"),
            is_calculated=CALCULATED_OVERRIDES.get(field["dbName"], bool(field["isCalculated"])),
            is_read_only=READ_ONLY_OVERRIDES.get(field["dbName"], bool(field.get("isReadOnly"))),
            decimals=field.get("decimals"),
            position=int(field["position"]),
            ordinal=int(field["ordinal"]),
            responsibility_block=field.get("responsibilityBlock"),
            formula=field.get("formula"),
            excel_formula=field.get("excelFormula"),
            rules=tuple(field.get("validation", {}).get("rules", ())),
            allow_null=bool(field.get("validation", {}).get("allowNull", True)),
        )
        for field in _load_form()["fields"]
        if field["dbName"] not in HIDDEN_FIELDS
    ]
    return tuple(sorted(metas, key=lambda meta: meta.position))


@lru_cache(maxsize=1)
def _by_name() -> dict[str, RenovationFieldMeta]:
    return {meta.db_name: meta for meta in field_metas()}


def field_meta(db_name: str) -> RenovationFieldMeta | None:
    return _by_name().get(db_name)


@lru_cache(maxsize=1)
def editable_fields() -> tuple[str, ...]:
    """Writable fields: neither calculated nor read-only (the overrides decide)."""
    return tuple(meta.db_name for meta in field_metas() if not meta.is_calculated and not meta.is_read_only)


@lru_cache(maxsize=1)
def formula_engine() -> FormulaEngine:
    """
    Compile using the same overrides and visible fields as the app metadata.

    Raw legacy flags marked the editable work code as calculated, causing
    materialization to erase it when another derived value was missing.
    """
    definitions = [
        FormulaDefinition(
            field=meta.db_name,
            cell_type=meta.cell_type,
            formula=meta.formula,
            is_calculated=meta.is_calculated,
            decimal_places=meta.decimals,
        )
        for meta in field_metas()
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


def field_header_paths() -> dict[str, str]:
    """Full display hierarchies, matching the grid's header-chain traversal."""
    cells = [cell for _, row in header_rows() for cell in row]
    fields = field_metas()
    block_labels = {
        "tech": "1) Техническая дирекция",
        "ksir": "2) Дирекция КСиР",
        "mto": "3) Дирекция МТО",
    }
    covered = {
        position
        for cell in cells
        if cell.level == 0
        for position in range(cell.start_position, cell.start_position + cell.col_span)
    }
    result = {}
    for field in fields:
        if field.db_name == "col_002":
            result[field.db_name] = "Предприятие"
            continue
        applicable = [
            cell
            for cell in cells
            if cell.start_position + 1 <= field.position < cell.start_position + cell.col_span + 1
        ]
        applicable.sort(
            key=lambda cell: (
                cell.level if cell.start_position in covered else 0,
                cell.start_position,
            )
        )
        texts: list[str] = []
        for cell in applicable:
            label = cell.text
            if cell.level == 0 and label.startswith("Блок "):
                block = next(
                    (
                        candidate.responsibility_block
                        for candidate in fields
                        if cell.start_position + 1 <= candidate.position < cell.start_position + cell.col_span + 1
                        and candidate.responsibility_block in block_labels
                    ),
                    None,
                )
                label = block_labels.get(block or "", label)
            if label and (not texts or texts[-1] != label):
                texts.append(label)
        result[field.db_name] = " → ".join(texts) or field.label
    return result
