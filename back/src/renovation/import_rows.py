"""Parse a technical-directorate Excel workbook into plan-row values (CPU-bound, thread-safe)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import BinaryIO

from src.renovation.form_schema import field_metas
from src.renovation.import_plan import (
    _CODE_HEADER,
    _REQUIRED_COLUMN_LABELS,
    _REQUIRED_FIELDS,
    _asset_column,
    _coerce,
    _column_name,
    _data_rows,
    _header_map,
    _is_blank,
    _is_reference_error,
    _iter_plan_rows,
    _locate_header,
    _missing_required_columns,
    _norm,
    _number_value,
    _read_rows,
    normalized_option,
)


@dataclass(slots=True)
class ParsedPlanRow:
    """One file row ready to be written: the work code, asset key and values."""

    code: str
    # Repair code («Наименование ДО»), or the asset NAME when the drop has no
    # such column and names the entity in «Предприятие» — `asset_is_name` says
    # which key the writer must resolve.
    asset_key: str
    values: dict[str, object]
    asset_is_name: bool = False


def parse_plan_rows(source: Path | BinaryIO, suffix: str | None = None) -> list[ParsedPlanRow]:
    """
    Read every data row of the plan sheet into plain values.

    Pure CPU work on the workbook (no database access), so the caller runs it
    in a worker thread and keeps the event loop free while a large file is
    parsed. Reference labels stay as TEXT here: the caller resolves them to
    option ids inside the write transaction.
    """
    return list(_parse_plan_rows(_read_rows(source, suffix)))


def iter_plan_rows(source: Path | BinaryIO, suffix: str | None = None) -> Iterator[ParsedPlanRow]:
    """Yield parsed rows directly from the workbook without retaining them all."""
    yield from _parse_plan_rows(_iter_plan_rows(source, suffix))


def iter_plan_row_batches(
    source: Path | BinaryIO, batch_size: int = 200, suffix: str | None = None
) -> Iterator[list[ParsedPlanRow]]:
    """Yield bounded parsed batches for an async writer/worker bridge."""
    batch: list[ParsedPlanRow] = []
    for row in iter_plan_rows(source, suffix):
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
    asset_index, assets_are_names = _asset_column(header_row)
    missing_columns = _missing_required_columns(columns, code_index, asset_index)
    if missing_columns:
        # The preflight refuses such a file before the load button is enabled;
        # the worker refuses it too, so a direct call cannot write a partial plan.
        msg = "Не найдены обязательные столбцы: " + ", ".join(missing_columns)
        raise ValueError(msg)
    assert code_index is not None  # noqa: S101 - the missing-columns check just refused the file
    assert asset_index is not None  # noqa: S101 - same
    data_rows: Iterable[list[object]] = chain(first_rows[data_start:], rows_iter)
    # The asset is the row's foreign key, not a plan column. An empty asset is
    # the ONLY reason to skip a row; every other required blank is an error.
    required_labels = dict(_REQUIRED_COLUMN_LABELS)
    required_columns = tuple(
        (index, required_labels[field])
        for field, index in columns.items()
        if field in _REQUIRED_FIELDS and field != "col_002"
    )
    field_by_name = {field.db_name: field for field in field_metas()}
    for row_number, raw in _data_rows(data_rows, data_start, code_index):
        raw_asset = raw[asset_index] if asset_index < len(raw) and raw[asset_index] is not None else ""
        # The writer resolves a name through the dictionary, whose canonical
        # spelling is what the plan references; the key normalizes the rest.
        asset_key = str(raw_asset).strip() if assets_are_names else str(raw_asset).strip().upper()
        if not asset_key:
            # Matching the preflight: a row without the asset is skipped, not
            # an error (unless the preflight finds every row like that).
            continue
        code = str(raw[code_index]).strip() if code_index < len(raw) and raw[code_index] is not None else ""
        empty_required = [label for index, label in required_columns if index >= len(raw) or _is_blank(raw[index])]
        if not code:
            empty_required.insert(0, _CODE_HEADER)
        if empty_required:
            # The preflight refuses the whole file when a required cell is
            # blank; a direct apply must not write a partial plan either.
            labels = ", ".join(f"«{label}»" for label in empty_required)
            msg = f"Строка {row_number}: не заполнены обязательные столбцы {labels}."
            raise ValueError(msg)
        values: dict[str, object] = {}
        for field, index in columns.items():
            meta = field_by_name.get(field)
            if meta is None:
                continue
            # A broken reference is refused even in a calculated column: the
            # preflight reports it, and a direct apply must not silently ignore
            # the broken source formula.
            if index < len(raw) and _is_reference_error(raw[index]):
                msg = f"Строка {row_number}: ошибка ссылки в формуле в столбце {_column_name(index)}."
                raise ValueError(msg)
            if meta.is_calculated:
                continue
            # Whitespace-only text is empty, exactly as in the preflight: it
            # must not become an empty reference label or a coerced value.
            if index >= len(raw) or _is_blank(raw[index]):
                continue
            if meta.catalog:
                # Excel hands a numeric option over as 3.0 while the catalog
                # label is "3": normalizing here keeps the analyzer and the
                # load agreeing on what a known option looks like.
                label = normalized_option(raw[index])
                if label is not None:
                    values[field] = label
                continue
            if meta.cell_type == "number":
                value, problem = _number_value(raw[index], meta)
                if problem is not None:
                    msg = (
                        f"Строка {row_number}: недопустимое значение в столбце {_column_name(index)} («{meta.label}»)."
                    )
                    raise ValueError(msg)
            else:
                value = _coerce(raw[index], meta.cell_type)
            if value is not None:
                values[field] = value
        yield ParsedPlanRow(code=code, asset_key=asset_key, values=values, asset_is_name=assets_are_names)
