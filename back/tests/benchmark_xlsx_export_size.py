"""
Reproducible, database-free XLSX export size baseline harness.

Run from the repository root (no DB, no production data — all fixtures are
deterministic synthetic rows; the writers under test are the real ones):

    uv run --directory back python -m tests.benchmark_xlsx_export_size run \
        --reps 3 --commit <sha> --out docs/XLSX_EXPORT_SIZE_BASELINE.json

    uv run --directory back python -m tests.benchmark_xlsx_export_size run \
        --workload plan_small_values --workload table_small --reps 1 --out NUL

`run` measures wall time over ``--reps`` timed renders and peak Python heap
over one extra tracemalloc render, then inspects the final archive with zipfile
and ElementTree. Time and memory are deliberately measured in separate passes:
tracemalloc is not free and would otherwise inflate the wall-clock baseline.

The workbook is written into ``BytesIO``. The production job writes to a file,
but both sinks are wrapped by ``xlsx_stream.UnseekableSink``, so the ZIP bytes
are identical; only the container differs (see the baseline report).

Nothing here mutates exporter code, the database, or any production artifact.
"""

import argparse
import json
import os
import platform
import re
import statistics
import sys
import time
import tracemalloc
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import UUID
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.utils import column_index_from_string

from src.common.export import ExportColumnRequest
from src.common.xlsx_table import TableWorkbookWriter
from src.renovation.export import RenovationWorkbookWriter, build_plan_sheet_layout
from src.renovation.form_schema import field_metas

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"m": _SHEET_NS}
_CELL_RE = re.compile(r"<c\b[^>]*(?:/>|>.*?</c>)", re.DOTALL)
_ROW_RE = re.compile(r"<row\b[^>]*>.*?</row>", re.DOTALL)
_VALUE_RE = re.compile(r"<v>.*?</v>|<f>.*?</f>|<is>.*?</is>", re.DOTALL)
_SHEET_PART_RE = re.compile(r"xl/worksheets/sheet\d+\.xml\Z")
_ROW_ATTR_RE = re.compile(r'\br="(\d+)"')
_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")

_REPEATED_LABELS = (
    "АО «БСК»",
    "АО «СНХЗ»",
    "ООО «ХимСнаб»",
    "Цех 01",
    "Цех 02",
    "Ремонт",
    "Плановый",
    "Без НДС",
)
_BASE_LABELS = ("Позиция", "Работа", "Материал", "Оборудование", "Услуга", "Группа")
_LONG_PREFIX = "Длинное описание материала с повторяющимся префиксом для проверки сжатия"
_LONG_SUFFIX = "конечная часть значения с уникальным индексом"
_ASSETS = ("АО «БСК»", "АО «СНХЗ»", "ООО «ХимСнаб»", "АО «Карболит»")
_REFERENCE_LABELS = {UUID(int=index + 1): f"Организация {index + 1:02d}" for index in range(24)}
_STATUSES = ("Активен", "Завершён", "Отменён", "Черновик")
_BATCH_ROWS = 500


def _emit(text: str) -> None:
    print(text)  # noqa: T201 — this is a CLI harness, stdout is its result


class _ZipEntry(TypedDict):
    name: str
    compressed_bytes: int
    uncompressed_bytes: int
    ratio: float | None
    compression_method: int
    flag_bits: int


class _Category(TypedDict):
    compressed_bytes: int
    uncompressed_bytes: int
    parts: int


class _Contributor(TypedDict):
    contributor: str
    uncompressed_bytes: int
    estimated_compressed_bytes: float | None
    estimated: bool


@dataclass(frozen=True)
class Workload:
    """One deterministic synthetic export shape."""

    id: str
    family: Literal["plan", "table"]
    rows: int
    profile: str
    values_only: bool
    block: str | None = None
    note: str = ""


def catalog() -> tuple[Workload, ...]:
    """Return the fixed workload matrix (row counts scaled only by --scale)."""
    return (
        Workload("plan_small_values", "plan", 20, "typical", values_only=True, note="fixed-overhead probe"),
        Workload(
            "plan_small_formulas", "plan", 20, "typical", values_only=False, note="fixed-overhead probe, live formulas"
        ),
        Workload("plan_typical_values", "plan", 1000, "typical", values_only=True, note="217-column plan, values"),
        Workload(
            "plan_typical_formulas", "plan", 1000, "typical", values_only=False, note="217-column plan, live formulas"
        ),
        Workload(
            "plan_large_values",
            "plan",
            10000,
            "typical",
            values_only=True,
            note="synthetic upper-end approximation",
        ),
        Workload("plan_repeated_text", "plan", 2000, "repeated", values_only=True, note="low-cardinality labels"),
        Workload(
            "plan_unique_long_text", "plan", 2000, "unique_long", values_only=True, note="high-cardinality long strings"
        ),
        Workload("plan_sparse", "plan", 2000, "sparse", values_only=True, note="mostly absent optional values"),
        Workload(
            "plan_nested_block_formulas",
            "plan",
            2000,
            "typical",
            values_only=False,
            block="mto",
            note="11-level header + compacted block merges + formulas",
        ),
        Workload("table_small_values", "table", 10, "typical", values_only=True, note="fixed-overhead probe"),
        Workload(
            "table_small_formulas", "table", 10, "typical", values_only=False, note="fixed-overhead probe, formulas"
        ),
        Workload("table_typical_values", "table", 1000, "typical", values_only=True, note="standard table, values"),
        Workload(
            "table_typical_formulas", "table", 1000, "typical", values_only=False, note="standard table, formulas"
        ),
        Workload("table_large_values", "table", 10000, "typical", values_only=True, note="long flat table"),
        Workload("table_repeated_text", "table", 2000, "repeated", values_only=True, note="low-cardinality labels"),
        Workload(
            "table_unique_long_text", "table", 2000, "unique_long", values_only=True, note="high-cardinality strings"
        ),
        Workload("table_sparse", "table", 2000, "sparse", values_only=True, note="mostly absent optional values"),
        Workload(
            "table_nested_formulas",
            "table",
            2000,
            "nested",
            values_only=False,
            note="3-level headers, hidden lists sheet, merges, alignments, formulas",
        ),
    )


def _scaled(workload: Workload, scale: float) -> Workload:
    rows = max(1, round(workload.rows * scale))
    return Workload(
        workload.id, workload.family, rows, workload.profile, workload.values_only, workload.block, workload.note
    )


def _plan_value(profile: str, index: int, column: int, cell_type: str) -> object:
    if profile == "sparse" and (index + column) % 8 != 0:
        return None
    if cell_type == "boolean":
        return bool((index + column) % 3)
    if cell_type == "date":
        return (date(2026, 1, 1) + timedelta(days=(index + column) % 365)).isoformat()
    if cell_type == "number":
        return ((index * 13 + column * 7) % 10000) / 100
    if cell_type == "reference":
        return str(UUID(int=((index + column) % 24) + 1))
    if profile == "repeated":
        return _REPEATED_LABELS[(index + column) % len(_REPEATED_LABELS)]
    if profile == "unique_long":
        return f"{_LONG_PREFIX} — запись {index:06d}-{column:03d} — {_LONG_SUFFIX}"
    return f"{_BASE_LABELS[column % len(_BASE_LABELS)]} {index % 64:03d}"


def _plan_rows(workload: Workload) -> Iterator[tuple[int, str, str, dict[str, object]]]:
    fields = field_metas()
    for index in range(workload.rows):
        values = {
            meta.db_name: _plan_value(workload.profile, index, position, meta.cell_type)
            for position, meta in enumerate(fields)
            if meta.db_name != "col_002"
        }
        yield index + 1, _ASSETS[index % len(_ASSETS)], f"RC-{index % 500:04d}", values


def _table_columns(workload: Workload) -> list[ExportColumnRequest]:
    specs: list[
        tuple[
            str,
            list[str],
            float,
            Literal["left", "center", "right"] | None,
            Literal["top", "center", "bottom"] | None,
        ]
    ] = [
        ("group", ["Группа", "Ремонт", "Объект"], 22.0, "left", "center"),
        ("org", ["Группа", "Ремонт", "Организация"], 30.0, None, None),
        ("work", ["Работы", "Наименование"], 34.0, None, None),
        ("asset", ["Работы", "Актив"], 20.0, None, None),
        ("base", ["Стоимость", "База"], 16.0, "right", None),
        ("amount", ["Стоимость", "Итого"], 16.0, "right", None),
        ("deviation", ["Стоимость", "Отклонение"], 16.0, "right", None),
        ("stamp", ["Служебные", "Время"], 22.0, None, "bottom"),
        ("flag", ["Служебные", "Признак"], 8.0, "center", None),
        ("status", ["Служебные", "Статус"], 14.0, None, None),
        ("tags", ["Служебные", "Метки"], 28.0, None, None),
    ]
    if workload.profile != "nested":
        specs = [(key, path[-1:], width, horizontal, vertical) for key, path, width, horizontal, vertical in specs]
    return [
        ExportColumnRequest(
            key=key,
            headerPath=path,
            width=width,
            horizontalAlignment=horizontal,
            verticalAlignment=vertical,
        )
        for key, path, width, horizontal, vertical in specs
    ]


class _TableRow:
    """Minimal ``model_dump`` row: DB/pydantic cost is outside this harness."""

    __slots__ = ("values",)

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def model_dump(self) -> dict[str, object]:
        """Return the column-keyed values the writer reads."""
        return self.values


def _table_value(workload: Workload, key: str, index: int) -> object:
    if workload.profile == "sparse" and key not in {"group", "base", "amount"} and index % 8 != 0:
        return None
    if key == "group":
        return f"Группа {(index // 10) % 12:02d}"
    if key == "org":
        if workload.profile == "repeated":
            return _REPEATED_LABELS[index % len(_REPEATED_LABELS)]
        if workload.profile == "unique_long":
            return f"{_LONG_PREFIX} — организация {index:06d} — {_LONG_SUFFIX}"
        return f"Организация {index % 40:03d}"
    if key == "work":
        return f"Работа {(index * 7) % 500:04d}"
    if key == "asset":
        return _ASSETS[index % len(_ASSETS)]
    if key in {"base", "amount"}:
        return ((index * (3 if key == "base" else 7)) % 10000) / 100
    if key == "deviation":
        return ((index * 4) % 10000) / 100
    if key == "stamp":
        return datetime(2026, 1, 1) + timedelta(days=index % 365, seconds=index % 60)  # noqa: DTZ001
    if key == "flag":
        return bool(index % 3)
    if key == "status":
        return _STATUSES[index % len(_STATUSES)]
    return ["Сырьё", "Ремонт"][index % 2]


def _table_rows(workload: Workload, columns: Sequence[ExportColumnRequest]) -> Iterator[_TableRow]:
    for index in range(workload.rows):
        yield _TableRow({column.key: _table_value(workload, column.key, index) for column in columns})


def _batches(rows: Iterator[Any]) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for row in rows:
        batch.append(row)
        if len(batch) == _BATCH_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def render(workload: Workload) -> bytes:
    """Render one synthetic workload through the real writer; return the archive bytes."""
    sink = BytesIO()
    totals_field = next(meta for meta in field_metas() if meta.db_name != "col_002" and meta.cell_type == "number")
    if workload.family == "plan":
        totals = {totals_field.db_name: float(workload.rows) * 1.5}
        layout = build_plan_sheet_layout(workload.block, totals, values_only=workload.values_only)
        writer = RenovationWorkbookWriter(sink, layout, _REFERENCE_LABELS, totals=totals, row_total=workload.rows)
        for batch in _batches(_plan_rows(workload)):
            writer.write_rows(batch)
    else:
        columns = _table_columns(workload)
        writer = TableWorkbookWriter(
            sink,
            "Синтетика",
            columns,
            header_pinned=True,
            lists={"statuses": list(_STATUSES)},
            column_lists={"status": "statuses"},
            calculated={} if workload.values_only else {"deviation": ("amount", "base")},
            row_total=workload.rows,
            totals_row={"amount": float(workload.rows) * 1.25, "base": float(workload.rows) * 0.75},
            merge_rows_by=("group",),
            values_only=workload.values_only,
        )
        for batch in _batches(_table_rows(workload, columns)):
            writer.write_rows(batch)
    writer.close()
    return sink.getvalue()


def _stats(values: Sequence[float | int]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "median": round(statistics.median(values), 6),
        "min": min(values),
        "max": max(values),
    }


def _peak_working_set_bytes() -> int | None:
    """OS high-water mark (monotonic per process) — a peak-memory proxy."""
    if sys.platform == "win32":
        try:
            import ctypes.wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
            info = kernel.K32GetProcessMemoryInfo
            info.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(Counters), ctypes.wintypes.DWORD]
            info.restype = ctypes.wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if info(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        except AttributeError, ImportError, OSError, ValueError:
            return None
    try:
        import resource
    except ImportError:
        return None
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # ty: ignore[missing-attribute]
    return int(max_rss) * 1024


def _style_signatures(xfs: Sequence[ET.Element]) -> list[tuple[object, ...]]:
    volatile = {"xfId", "applyFont", "applyBorder", "applyNumberFormat", "applyAlignment"}
    signatures: list[tuple[object, ...]] = []
    for xf in xfs:
        attributes = tuple(sorted((key, value) for key, value in xf.attrib.items() if key not in volatile))
        children = tuple((child.tag, tuple(sorted(child.attrib.items()))) for child in xf if isinstance(child.tag, str))
        signatures.append((attributes, children))
    return signatures


def _inspect_styles(raw: bytes) -> dict[str, Any]:
    root = ET.fromstring(raw)  # noqa: S314 — generated by the writer under test
    result: dict[str, Any] = {}
    for tag in ("numFmts", "fonts", "fills", "borders", "cellStyleXfs", "cellXfs", "cellStyles"):
        element = root.find(f"m:{tag}", _NS)
        result[tag] = len(element) if element is not None else 0
    cell_xfs = root.find("m:cellXfs", _NS)
    xfs = list(cell_xfs) if cell_xfs is not None else []
    canonical = [ET.tostring(xf, encoding="unicode") for xf in xfs]
    signatures = _style_signatures(xfs)
    return {
        **result,
        "duplicate_exact_xfs": len(canonical) - len(set(canonical)),
        "duplicate_near_identical_xfs": len(signatures) - len(set(signatures)),
    }


def _inspect_shared_strings(archive: zipfile.ZipFile) -> dict[str, Any]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return {"present": False, "items": 0, "characters": 0}
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))  # noqa: S314 — generated workbook
    items = root.findall("m:si", _NS)
    characters = sum(len(node.text or "") for item in items for node in item.iter(f"{{{_SHEET_NS}}}t"))
    return {"present": True, "items": len(items), "characters": characters}


def _inspect_sheet(raw: bytes, *, header_rows_count: int, profile_name: str) -> dict[str, Any]:
    text = raw.decode("utf-8")
    root = ET.fromstring(raw)  # noqa: S314 — generated by the writer under test
    rows = root.findall("m:sheetData/m:row", _NS)
    cells = 0
    non_empty = 0
    styled_empty = 0
    bare_empty = 0
    formula_count = 0
    text_characters = 0
    kinds: Counter[str] = Counter()
    styles: Counter[str] = Counter()
    last_row = 0
    last_column = 0
    for row in rows:
        row_number = int(row.attrib.get("r", 0))
        last_row = max(last_row, row_number)
        for cell in row:
            if cell.tag != f"{{{_SHEET_NS}}}c":
                continue
            cells += 1
            style = cell.attrib.get("s", "0")
            styles[style] += 1
            reference = cell.attrib.get("r", "")
            match = _REF_RE.match(reference)
            if match:
                last_column = max(last_column, column_index_from_string(match.group(1)))
            has_formula = cell.find("m:f", _NS) is not None
            has_value = cell.find("m:v", _NS) is not None
            inline = cell.find("m:is", _NS)
            inline_text = (
                "".join(node.text or "" for node in inline.iter(f"{{{_SHEET_NS}}}t")) if inline is not None else ""
            )
            if has_formula:
                formula_count += 1
                kinds["formula"] += 1
            elif inline is not None and inline_text:
                kinds["inline_string"] += 1
                text_characters += len(inline_text)
            elif has_value and cell.attrib.get("t") == "b":
                kinds["boolean"] += 1
            elif has_value:
                kinds["number_or_serial"] += 1
            elif style != "0":
                styled_empty += 1
                kinds["styled_empty"] += 1
            else:
                bare_empty += 1
                kinds["empty"] += 1
            if has_formula or has_value or inline_text:
                non_empty += 1
    dimension_element = root.find("m:dimension", _NS)
    dimension_ref = dimension_element.attrib.get("ref") if dimension_element is not None else None
    dimension_last_row = last_row
    dimension_last_column = last_column
    if dimension_ref and ":" in dimension_ref:
        end = dimension_ref.split(":", 1)[1]
        match = _REF_RE.match(end)
        if match:
            dimension_last_row = int(match.group(2))
            dimension_last_column = column_index_from_string(match.group(1))
    pane = root.find("m:sheetViews/m:sheetView/m:pane", _NS)
    prefix_end = text.index("<sheetData>")
    tail_start = text.rindex("</sheetData>")
    prefix = text[: prefix_end + len("<sheetData>")]
    tail = text[tail_start:]
    body = text[prefix_end + len("<sheetData>") : tail_start]
    header_bytes = 0
    data_bytes = 0
    data_value_bytes = 0
    data_cell_bytes = 0
    data_row_wrapper_bytes = 0
    header_rows = 0
    data_rows = 0
    for match in _ROW_RE.finditer(body):
        segment = match.group(0)
        row_match = _ROW_ATTR_RE.search(segment)
        row_number = int(row_match.group(1)) if row_match else 0
        segment_bytes = len(segment.encode("utf-8"))
        if row_number <= header_rows_count:
            header_bytes += segment_bytes
            header_rows += 1
            continue
        data_rows += 1
        data_bytes += segment_bytes
        cell_segments = [cell.group(0) for cell in _CELL_RE.finditer(segment)]
        cell_bytes = sum(len(cell.encode("utf-8")) for cell in cell_segments)
        value_bytes = sum(len(value.encode("utf-8")) for value in _VALUE_RE.findall(segment))
        data_value_bytes += value_bytes
        data_cell_bytes += cell_bytes
        open_tag_end = segment.index(">") + 1
        close_tag_start = segment.rindex("</row>")
        data_row_wrapper_bytes += len(segment[:open_tag_end].encode("utf-8")) + len(
            segment[close_tag_start:].encode("utf-8")
        )
    cols_match = re.search(r"<cols>.*?</cols>", prefix, re.DOTALL)
    merge_match = re.search(r"<mergeCells\b.*?</mergeCells>", tail, re.DOTALL)
    validation_match = re.search(r"<dataValidations\b.*?</dataValidations>", tail, re.DOTALL)
    return {
        "part": profile_name,
        "rows_serialized": len(rows),
        "data_rows": data_rows,
        "header_or_aggregate_rows": header_rows,
        "cells_serialized": cells,
        "non_empty_cells": non_empty,
        "styled_empty_cells": styled_empty,
        "bare_empty_cells": bare_empty,
        "value_kinds": dict(kinds),
        "formula_count": formula_count,
        "inline_text_characters": text_characters,
        "style_id_counts": dict(styles.most_common()),
        "distinct_style_ids_used": len(styles),
        "dimension_ref": dimension_ref,
        "dimension_end_row": dimension_last_row,
        "actual_last_row": last_row,
        "dimension_end_column_index": dimension_last_column,
        "actual_last_column_index": last_column,
        "dimension_inflated": dimension_last_row > last_row or dimension_last_column > last_column,
        "column_definitions": len(root.findall("m:cols/m:col", _NS)),
        "merge_range_count": len(root.findall("m:mergeCells/m:mergeCell", _NS)),
        "validation_count": len(root.findall("m:dataValidations/m:dataValidation", _NS)),
        "frozen_pane": (
            {
                "xSplit": pane.attrib.get("xSplit"),
                "ySplit": pane.attrib.get("ySplit"),
                "topLeftCell": pane.attrib.get("topLeftCell"),
            }
            if pane is not None
            else None
        ),
        "byte_profile": {
            "total": len(raw),
            "prefix_total": len(prefix.encode("utf-8")),
            "column_definitions": len(cols_match.group(0).encode("utf-8")) if cols_match else 0,
            "header_and_aggregate_rows": header_bytes,
            "data_rows_total": data_bytes,
            "data_cell_value_content": data_value_bytes,
            "data_cell_wrapper_overhead": data_cell_bytes - data_value_bytes,
            "data_row_wrapper_overhead": data_row_wrapper_bytes,
            "tail_total": len(tail.encode("utf-8")),
            "merge_cells_block": len(merge_match.group(0).encode("utf-8")) if merge_match else 0,
            "validations_block": len(validation_match.group(0).encode("utf-8")) if validation_match else 0,
            "unattributed_body": len(body.encode("utf-8")) - header_bytes - data_bytes,
        },
    }


def inspect(data: bytes, *, header_rows_count: int, main_sheet: str) -> dict[str, Any]:
    """Return ZIP/OPC/XML evidence for one generated workbook."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        entries: list[_ZipEntry] = [
            {
                "name": info.filename,
                "compressed_bytes": info.compress_size,
                "uncompressed_bytes": info.file_size,
                "ratio": round(info.file_size / info.compress_size, 4) if info.compress_size else None,
                "compression_method": info.compress_type,
                "flag_bits": info.flag_bits,
            }
            for info in infos
        ]
        uncompressed = sum(info.file_size for info in infos)
        compressed = sum(info.compress_size for info in infos)
        sheets: dict[str, dict[str, Any]] = {}
        for info in infos:
            if not _SHEET_PART_RE.match(info.filename):
                continue
            raw = archive.read(info.filename)
            profile = _inspect_sheet(
                raw,
                header_rows_count=header_rows_count,
                profile_name=info.filename,
            )
            profile["compressed_bytes"] = info.compress_size
            sheets[info.filename] = profile
        styles = _inspect_styles(archive.read("xl/styles.xml"))
        shared = _inspect_shared_strings(archive)
    by_name = {entry["name"]: entry for entry in entries}
    categories: dict[str, _Category] = {}
    for entry in entries:
        name = entry["name"]
        if name.startswith("xl/worksheets/"):
            category = "worksheets"
        elif name == "xl/sharedStrings.xml":
            category = "shared_strings"
        elif name == "xl/styles.xml":
            category = "styles"
        elif name == "xl/workbook.xml":
            category = "workbook"
        elif name.startswith("xl/theme/"):
            category = "theme"
        elif name.startswith("xl/media/"):
            category = "media"
        elif name.startswith("xl/drawings/"):
            category = "drawings"
        elif "comment" in name.lower():
            category = "comments"
        elif name.startswith("xl/pivotCache/"):
            category = "pivot_caches"
        elif name.startswith("xl/externalLinks/"):
            category = "external_links"
        else:
            category = "other"
        totals = categories.setdefault(category, {"compressed_bytes": 0, "uncompressed_bytes": 0, "parts": 0})
        totals["compressed_bytes"] += entry["compressed_bytes"]
        totals["uncompressed_bytes"] += entry["uncompressed_bytes"]
        totals["parts"] += 1
    main = sheets.get(main_sheet)
    if main is None:
        message = f"Missing expected sheet part {main_sheet}"
        raise AssertionError(message)
    main_compressed = by_name[main_sheet]["compressed_bytes"]
    main_profile = main["byte_profile"]
    main_total = int(main_profile["total"])
    buckets: list[tuple[str, int]] = [
        ("sheet data values (<v>/<f>/<is> content)", main_profile["data_cell_value_content"]),
        ("sheet cell XML wrappers (refs, styles, tags)", main_profile["data_cell_wrapper_overhead"]),
        ("sheet header/aggregate rows", main_profile["header_and_aggregate_rows"]),
        ("sheet row wrappers", main_profile["data_row_wrapper_overhead"]),
        ("sheet prefix/cols/tail (merges, validations)", main_profile["prefix_total"] + main_profile["tail_total"]),
        ("xl/styles.xml", by_name["xl/styles.xml"]["uncompressed_bytes"]),
        (
            "other package parts",
            uncompressed - main_total - by_name["xl/styles.xml"]["uncompressed_bytes"],
        ),
    ]
    ranked: list[_Contributor] = [
        {
            "contributor": name,
            "uncompressed_bytes": size,
            "estimated_compressed_bytes": round(main_compressed * size / main_total, 1)
            if name.startswith("sheet ")
            else (by_name["xl/styles.xml"]["compressed_bytes"] if name == "xl/styles.xml" else None),
            "estimated": True,
        }
        for name, size in buckets
    ]
    ranked.sort(key=lambda item: item["uncompressed_bytes"], reverse=True)
    return {
        "final_bytes": len(data),
        "zip": {
            "entry_count": len(entries),
            "compressed_bytes_sum": compressed,
            "uncompressed_bytes_sum": uncompressed,
            "compression_ratio": round(uncompressed / compressed, 4) if compressed else None,
            "entries": sorted(entries, key=lambda entry: entry["compressed_bytes"], reverse=True),
            "categories": categories,
        },
        "sheets": sheets,
        "styles": styles,
        "shared_strings": shared,
        "formula_count": sum(sheet["formula_count"] for sheet in sheets.values()),
        "ranked_contributors": ranked,
    }


def run_one(workload: Workload, reps: int) -> dict[str, Any]:
    """Measure one workload; return timings, memory and a full OPC inspection."""
    durations: list[float] = []
    sizes: list[int] = []
    data = b""
    for _ in range(reps):
        started = time.perf_counter()
        data = render(workload)
        durations.append(time.perf_counter() - started)
        sizes.append(len(data))
    peak_ws_before = _peak_working_set_bytes()
    tracemalloc.start()
    tracemalloc.reset_peak()
    data = render(workload)
    python_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    peak_ws_after = _peak_working_set_bytes()
    header_rows_count = 11 + 2 if workload.family == "plan" else (3 if workload.profile == "nested" else 1) + 1
    inspection = inspect(
        data,
        header_rows_count=header_rows_count,
        main_sheet="xl/worksheets/sheet1.xml",
    )
    main = inspection["sheets"]["xl/worksheets/sheet1.xml"]
    return {
        "workload": {
            "id": workload.id,
            "family": workload.family,
            "rows": workload.rows,
            "profile": workload.profile,
            "values_only": workload.values_only,
            "block": workload.block,
            "note": workload.note,
        },
        "final_bytes": _stats(sizes),
        "wall_seconds": _stats(durations),
        "peak_python_heap_bytes": python_peak,
        "os_peak_working_set_before": peak_ws_before,
        "os_peak_working_set_after": peak_ws_after,
        "rows": main["rows_serialized"],
        "columns": max(main["dimension_end_column_index"], 1),
        "non_empty_cells": main["non_empty_cells"],
        "cells_serialized": main["cells_serialized"],
        "sheet_count": len(inspection["sheets"]),
        "formula_count": inspection["formula_count"],
        "shared_strings": inspection["shared_strings"],
        "style_records": inspection["styles"],
        "inspection": inspection,
    }


def environment(commit: str) -> dict[str, Any]:
    """Record the runtime and library context of a run."""
    return {
        "commit": commit,
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "os_name": os.name,
        "openpyxl": openpyxl.__version__,
        "zlib": zlib.ZLIB_VERSION,
        "zip64_limit": zipfile.ZIP64_LIMIT,
        "zip_compression": "zipfile.ZIP_DEFLATED, compresslevel=1 (both real writers)",
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "fixtures": "deterministic synthetic rows only; no database, no production data",
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run"], help="run the full matrix")
    parser.add_argument("--reps", type=int, default=3, help="timed renders per workload (default: 3)")
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every workload row count")
    parser.add_argument("--workload", action="append", dest="workloads", help="limit the matrix to one workload id")
    parser.add_argument("--out", type=Path, default=Path("docs/XLSX_EXPORT_SIZE_BASELINE.json"))
    parser.add_argument("--commit", default="unknown", help="git SHA of the code under measurement")
    args = parser.parse_args(argv)
    if args.reps < 1:
        parser.error("--reps must be >= 1")
    if args.scale <= 0:
        parser.error("--scale must be > 0")
    selected = tuple(_scaled(item, args.scale) for item in catalog())
    if args.workloads:
        known = {item.id for item in selected}
        unknown = set(args.workloads) - known
        if unknown:
            parser.error(f"unknown workload(s): {sorted(unknown)}")
        selected = tuple(item for item in selected if item.id in args.workloads)
    results = [run_one(workload, args.reps) for workload in selected]
    report = {
        "environment": environment(args.commit),
        "method": {
            "timed_renders": args.reps,
            "memory_render": 1,
            "memory_metric": "tracemalloc peak (Python heap), OS peak working set as monotonic proxy",
            "compression_ratio": "uncompressed bytes / compressed bytes",
            "inspection": "zipfile metadata + ElementTree XML + raw-sheet regex byte profile",
        },
        "workloads": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        info = result["workload"]
        _emit(
            f"{info['id']:26s} rows={info['rows']:6d} "
            f"bytes={result['final_bytes']['median']:>10} "
            f"time={result['wall_seconds']['median']:>9}s "
            f"heap={result['peak_python_heap_bytes']:>12} "
            f"forms={result['formula_count']:>7}"
        )
    _emit(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
