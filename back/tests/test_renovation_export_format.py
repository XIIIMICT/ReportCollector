"""
XLSX format regressions for the renovation plan export (no database).

Excel rejects workbooks whose ``<row>`` elements come out of `r` order or
whose ``<mergeCell>`` block repeats a range — the "файл повреждён, открыть и
восстановить?" repair dialog (and a streaming response can never un-send the
bytes). The plan export fuses header strips, an aggregating totals row and
thousands of data rows into one sheet, so the invariants are pinned by
parsing the raw ``sheet1.xml`` directly (openpyxl silently re-serializes and
would hide exactly the broken intermediate state).
"""

import struct
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest
from openpyxl.utils import get_column_letter

from src.renovation.export import (
    ExportRowCountMismatchError,
    ExportTooLargeError,
    RenovationWorkbookWriter,
    build_plan_sheet_layout,
)
from src.renovation.form_schema import RenovationFieldMeta, field_metas, header_rows

_SHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

_REPAIR_CODE = "2027_BSKR"
_ROWS_TEMPLATE: list[tuple[int, str, str, dict[str, object]]] = [
    (1, "АО «БСК»", _REPAIR_CODE, {"workshop_name": 1.0}),
    (2, "АО «БСК»", _REPAIR_CODE, {}),
]


def _numeric_totals_field() -> RenovationFieldMeta:
    """Return the first additive non-asset numeric field — a realistic totals key."""
    return next(meta for meta in field_metas() if meta.db_name != "col_002" and meta.cell_type == "number")


def _totals_column(totals_field: RenovationFieldMeta) -> str:
    layout = build_plan_sheet_layout(None)
    return get_column_letter(layout.column_by_field[totals_field.db_name])


def _render(
    rows: list[tuple[int, str, str, dict[str, object]]],
    totals: dict[str, float],
    row_total: int | None,
    values_only: bool = True,
) -> bytes:
    sink = BytesIO()
    layout = build_plan_sheet_layout(None, totals, values_only=values_only)
    writer = RenovationWorkbookWriter(sink, layout, {}, totals=totals, row_total=row_total)
    writer.write_rows(rows)
    writer.close()
    return sink.getvalue()


def _sheets(data: bytes) -> ET.Element:
    """Parse the sheet part of a generated workbook as raw XML."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        # S314: the bytes are produced by the writer under test right here...
        return ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314


def _row_numbers(sheet_xml: ET.Element) -> list[int]:
    rows = sheet_xml.findall("m:sheetData/m:row", _SHEET_NS)
    return [int(row.attrib["r"]) for row in rows]


def _merge_refs(sheet_xml: ET.Element) -> list[str]:
    return [cell.attrib["ref"] for cell in sheet_xml.findall("m:mergeCells/m:mergeCell", _SHEET_NS)]


def test_rows_are_ascending_and_totals_precede_data():
    """`<row r>` must be strictly increasing; row 13 carries the totals, 14+ the data."""
    totals_field = _numeric_totals_field()
    data = _render(_ROWS_TEMPLATE, {totals_field.db_name: 3.0}, row_total=2)
    numbers = _row_numbers(_sheets(data))
    header_levels = len(header_rows())
    assert header_levels == 11
    # 11 header levels + the number strip (12) + the totals row (13), then the
    # data rows in one dense ascending sequence.
    assert numbers == list(range(1, header_levels + 3 + len(_ROWS_TEMPLATE)))


def test_totals_row_sums_the_exact_data_range():
    """The live SUM covers precisely rows 14..15 for two data rows — no off-by-one."""
    totals_field = _numeric_totals_field()
    data = _render(_ROWS_TEMPLATE, {totals_field.db_name: 3.0}, row_total=2, values_only=False)
    totals_row = _sheets(data).findall("m:sheetData/m:row", _SHEET_NS)[12]
    assert totals_row.attrib["r"] == "13"
    column = _totals_column(totals_field)
    cell = totals_row.find(f"m:c[@r='{column}13']", _SHEET_NS)
    assert cell is not None
    assert cell.attrib["s"] == "7"
    formula = cell.find("m:f", _SHEET_NS)
    assert formula is not None
    assert formula.text == f"SUM({column}14:{column}15)"
    cache = cell.find("m:v", _SHEET_NS)
    assert cache is not None
    assert cache.text == "3.0"


def test_merge_cell_refs_are_unique():
    """A repeated mergeCell range rejected whole workbooks; the band merge must appear once."""
    totals_field = _numeric_totals_field()
    data = _render(_ROWS_TEMPLATE, {totals_field.db_name: 3.0}, row_total=2)
    sheet_xml = _sheets(data)
    refs = _merge_refs(sheet_xml)
    assert len(refs) == len(set(refs))
    # The sheet starts with the «Предприятие» column (A) — there is NO
    # row-number column, so no corner block and no «№»/«Итог» labels. The
    # first form column (work_or_repair_code) is B; a column whose totals cell is empty
    # merges its number over the totals row.
    assert refs.count("B12:B13") == 1
    assert not any(ref.startswith("A1:") for ref in refs), "no corner block: the asset header owns column A"
    row_texts = [row.text or "" for row in sheet_xml.findall("m:sheetData/m:row", _SHEET_NS)]
    assert "№" not in row_texts[11], "the number strip must not carry a corner «№»"
    assert "Итог" not in row_texts[12], "the summary row carries no «Итог» label"


def test_totals_keep_cached_value_without_a_row_count():
    """Without (or with zero) promised rows the totals carry the cached value only."""
    totals_field = _numeric_totals_field()
    column = _totals_column(totals_field)
    for row_total in (None, 0):
        sheet_xml = _sheets(_render([], {totals_field.db_name: 42.5}, row_total=row_total))
        totals_row = sheet_xml.findall("m:sheetData/m:row", _SHEET_NS)[12]
        cell = totals_row.find(f"m:c[@r='{column}13']", _SHEET_NS)
        assert cell is not None
        assert cell.find("m:f", _SHEET_NS) is None
        cache = cell.find("m:v", _SHEET_NS)
        assert cache is not None
        assert cache.text == "42.5"


def test_writer_fails_fast_on_row_count_mismatch():
    """More or fewer rows than promised must fail the write, not the output file."""
    totals_field = _numeric_totals_field()
    totals = {totals_field.db_name: 1.0}
    layout = build_plan_sheet_layout(None, totals)
    # Too many rows: the second one breaks the promised count...
    writer = RenovationWorkbookWriter(BytesIO(), layout, {}, totals=totals, row_total=1)
    writer.write_rows(_ROWS_TEMPLATE[:1])
    with pytest.raises(ExportRowCountMismatchError):
        writer.write_rows(_ROWS_TEMPLATE[1:])
    # ...and too few rows: close() learns the promise cannot be kept.
    writer = RenovationWorkbookWriter(BytesIO(), layout, {}, totals=totals, row_total=2)
    writer.write_rows(_ROWS_TEMPLATE[:1])
    with pytest.raises(ExportRowCountMismatchError):
        writer.close()
    # The exact count still closes cleanly.
    writer = RenovationWorkbookWriter(BytesIO(), layout, {}, totals=totals, row_total=2)
    writer.write_rows(_ROWS_TEMPLATE)
    writer.close()


def _local_header(data: bytes, part: str) -> tuple[int, int, int, int, int, int]:
    """Return (version_needed, flag_bits, crc, csize, usize, extra_len) of an entry's local header."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo(part)
        offset = info.header_offset
    raw = data[offset : offset + 64]
    _sig, version, flags, _method, _time, _date, crc, csize, usize, _name_len, extra_len = struct.unpack(
        "<IHHHHHIIIHH", raw[:30]
    )
    return version, flags, crc, csize, usize, extra_len


def test_archive_is_append_only_streamable():
    """
    The sheet entry must be a data-descriptor record, never a patched header.

    zipfile on a seekable sink first writes a draft local header (zero CRC and
    sizes) and only rewrites it by seeking back at close(); the download
    streamer tails the growing file, so the browser receives the draft bytes
    and never the patch — the downloaded workbook then carries an invalid
    local header and Excel repair-dialogs it (a 520 MB real export hit
    exactly this). The writer therefore wraps its sink append-only: the header
    honestly flags "sizes follow the data" (bit 3), stays plain ZIP — a
    streamed zip64 entry carries an all-zero zip64 extra that real Excel
    rejects — and every byte is written exactly once, in order.
    """
    totals_field = _numeric_totals_field()
    data = _render(_ROWS_TEMPLATE, {totals_field.db_name: 3.0}, row_total=2)
    for part in ("xl/worksheets/sheet1.xml", "xl/styles.xml"):
        version, flags, crc, csize, usize, extra_len = _local_header(data, part)
        assert flags & 0x0008, f"{part}: the entry must carry the data-descriptor flag"
        assert crc == 0, f"{part}: crc must live in the descriptor, not the draft header"
        assert csize == 0, f"{part}: the compressed size must not sit in the draft header"
        assert usize == 0, f"{part}: the uncompressed size must not sit in the draft header"
        assert version < 45, f"{part}: a streamed zip64 entry's zeroed extra field is rejected by Excel"
        assert extra_len == 0, f"{part}: no extra fields expected on a plain entry"
    # The served bytes and the finalized file are the same thing: the central
    # directory carries the true sizes, and the whole archive is readable.
    with zipfile.ZipFile(BytesIO(data)) as archive:
        assert archive.read("xl/workbook.xml").startswith(b"<?xml")
        assert archive.testzip() is None


def test_writer_fails_cleanly_past_the_plain_zip_ceiling(monkeypatch):
    """A sheet past the plain-ZIP ceiling (4 GiB) must fail with the user-facing error.

    The append-only container cannot fall back to zip64 (a streamed zip64
    entry's zeroed local extra is Excel-rejected), so an oversized sheet fails
    the job loudly instead of producing a workbook Excel would repair. The
    ceiling is simulated by shrinking ``ZIP64_LIMIT`` — the writer's wrapper
    translates zipfile's bare ``RuntimeError`` into ``ExportTooLargeError``.
    """
    import src.common.xlsx_stream  # noqa: F401  # its import restores the real 4 GiB limit

    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 10)
    totals_field = _numeric_totals_field()
    totals = {totals_field.db_name: 1.0}
    writer = RenovationWorkbookWriter(BytesIO(), build_plan_sheet_layout(None, totals), {}, totals=totals, row_total=1)
    writer.write_rows(_ROWS_TEMPLATE[:1])
    with pytest.raises(ExportTooLargeError, match="4 ГБ"):
        writer.close()
