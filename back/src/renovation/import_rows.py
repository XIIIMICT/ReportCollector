"""Parse a technical-directorate XLSB file into plan-row values (CPU-bound, thread-safe)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import BinaryIO

from src.renovation.form_schema import field_metas
from src.renovation.import_plan import (
    _ASSET_HEADER,
    _CODE_HEADER,
    _coerce,
    _header_map,
    _is_numbering_row,
    _iter_plan_rows,
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
    return list(_parse_plan_rows(_read_rows(source)))


def iter_plan_rows(source: Path | BinaryIO) -> Iterator[ParsedPlanRow]:
    """Yield parsed rows directly from the workbook without retaining them all."""
    yield from _parse_plan_rows(_iter_plan_rows(source))


def iter_plan_row_batches(source: Path | BinaryIO, batch_size: int = 200) -> Iterator[list[ParsedPlanRow]]:
    """Yield bounded parsed batches for an async writer/worker bridge."""
    batch: list[ParsedPlanRow] = []
    for row in iter_plan_rows(source):
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _parse_plan_rows(rows: Iterable[list[object]]) -> Iterator[ParsedPlanRow]:
    """Parse rows from a materialized fixture or a streaming workbook iterator."""
    rows_iter = iter(rows)
    first_rows = list(islice(rows_iter, 2))
    header_row, data_start = _locate_header(first_rows)
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
    data_rows: Iterable[list[object]] = chain(first_rows[data_start:], rows_iter)
    first_data_row = next(iter(data_rows), None)
    if first_data_row is None:
        data_rows = iter(())
    elif not _is_numbering_row(first_data_row, code_index):
        data_rows = chain((first_data_row,), data_rows)
    field_by_name = {field.db_name: field for field in field_metas()}
    for raw in data_rows:
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
        yield ParsedPlanRow(code=code, asset_code=asset_code, values=values)
