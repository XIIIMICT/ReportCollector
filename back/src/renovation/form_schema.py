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
