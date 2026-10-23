"""Parse a technical-directorate XLSB file into plan-row values (CPU-bound, thread-safe)."""

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from src.renovation.form_schema import field_metas
from src.renovation.import_plan import (
    _ASSET_HEADER,
    _CODE_HEADER,
    _coerce,
    _header_map,
    _is_numbering_row,
    _locate_header,
    _norm,
    _read_rows,
    normalized_option,
)


@dataclass(slots=True)
class ParsedPlanRow:
    """One file row ready to be written: the work code, asset code and values."""

    code: str
    asset_code: str
    values: dict[str, object]


def parse_plan_rows(source: Path | BinaryIO) -> list[ParsedPlanRow]:
    """
    Read every data row of the plan sheet into plain values.

    Pure CPU work on the workbook (no database access), so the caller runs it
    in a worker thread and keeps the event loop free while a large file is
    parsed. Reference labels stay as TEXT here: the caller resolves them to
    option ids inside the write transaction.
    """
    rows = _read_rows(source)
    header_row, data_start = _locate_header(rows)
    if header_row is None:
        msg = "no plan sheet header"
        raise ValueError(msg)
    mapping = _header_map()
    columns: dict[str, int] = {}
    for index, value in enumerate(header_row):
        normalized = _norm(value)
        if not normalized:
            continue
        field = mapping.get(normalized)
        if field is None and normalized.startswith(_norm("Дефектная ведомость/ВОР")):
            field = "col_023"
        if field is not None:
            columns.setdefault(field, index)
    code_index = next((i for i, value in enumerate(header_row) if _norm(value) == _norm(_CODE_HEADER)), None)
    asset_index = next((i for i, value in enumerate(header_row) if _norm(value) == _norm(_ASSET_HEADER)), None)
    if code_index is None or asset_index is None:
        msg = "no work-code column"
        raise ValueError(msg)
    if data_start < len(rows) and _is_numbering_row(rows[data_start], code_index):
        data_start += 1
    field_by_name = {field.db_name: field for field in field_metas()}
    parsed: list[ParsedPlanRow] = []
    for raw in rows[data_start:]:
        if not any(value not in (None, "") for value in raw):
            continue
        code = str(raw[code_index]).strip() if code_index < len(raw) and raw[code_index] is not None else ""
        if not code:
            continue
        asset_code = (
            str(raw[asset_index]).strip().upper() if asset_index < len(raw) and raw[asset_index] is not None else ""
        )
        values: dict[str, object] = {}
        for field, index in columns.items():
            meta = field_by_name.get(field)
            if meta is None or meta.is_calculated:
                continue
            if index >= len(raw) or raw[index] in (None, ""):
                continue
            if meta.catalog:
                # Excel hands a numeric option over as 3.0 while the catalog
                # label is "3": normalizing here keeps the analyzer and the
                # load agreeing on what a known option looks like.
                label = normalized_option(raw[index])
                if label is not None:
                    values[field] = label
                continue
            value = _coerce(raw[index], meta.cell_type)
            if value is not None:
                values[field] = value
        parsed.append(ParsedPlanRow(code=code, asset_code=asset_code, values=values))
    return parsed
