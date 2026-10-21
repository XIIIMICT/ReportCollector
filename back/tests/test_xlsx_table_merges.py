from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook
from pydantic import BaseModel

from src.common.xlsx_table import TableWorkbookWriter


class _Column(BaseModel):
    """Minimal export column (the writer's ``ExportColumnLike`` protocol)."""

    key: str
    headerPath: list[str]
    width: float = 20.0
    numberFormat: str | None = None
    horizontalAlignment: str | None = None
    verticalAlignment: str | None = None


class _Row(BaseModel):
    group: str | None
    label: str | None


COLUMNS = [_Column(key="group", headerPath=["Группа"]), _Column(key="label", headerPath=["Метка"])]


def _render(batches: list[list[_Row]]) -> list[str]:
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        "Лист",
        COLUMNS,
        header_pinned=True,
        row_total=sum(len(batch) for batch in batches),
        merge_rows_by=("group",),
    )
    for batch in batches:
        writer.write_rows(batch)
    writer.close()
    sheet = load_workbook(BytesIO(sink.getvalue())).worksheets[0]
    return sorted(str(span) for span in sheet.merged_cells.ranges if span.min_row >= 2)


def test_runs_merge_vertically_and_span_batches() -> None:
    """A run is a maximal block of equal values; a batch boundary must not split it."""
    merged = _render(
        [
            [_Row(group="A", label="1"), _Row(group="A", label="2")],
            # The same group continues into the next batch (rows stream in chunks).
            [_Row(group="A", label="3"), _Row(group="B", label="4"), _Row(group="B", label="5")],
            [_Row(group="C", label="6")],
        ]
    )
    assert merged == ["A2:A4", "A5:A6"]


def test_a_single_row_run_is_not_merged() -> None:
    """A one-row range is not a merge — Excel rejects it as a no-op."""
    assert _render([[_Row(group="A", label="1"), _Row(group="B", label="2")]]) == []


def test_empty_values_never_merge() -> None:
    """A blank block stays blank cells, and a blank row does not bridge two groups."""
    merged = _render(
        [
            [_Row(group=None, label="1"), _Row(group=None, label="2")],
            [_Row(group="A", label="3"), _Row(group="A", label="4")],
        ]
    )
    assert merged == ["A4:A5"]


def test_without_merge_rows_by_nothing_is_merged() -> None:
    """The option is opt-in: every other table keeps its plain data block."""
    sink = BytesIO()
    writer = TableWorkbookWriter(sink, "Лист", COLUMNS, header_pinned=True, row_total=2)
    writer.write_rows([_Row(group="A", label="1"), _Row(group="A", label="2")])
    writer.close()
    sheet = load_workbook(BytesIO(sink.getvalue())).worksheets[0]
    assert [str(span) for span in sheet.merged_cells.ranges if span.min_row >= 2] == []


def test_column_alignment_survives_merges_and_keeps_freeze_pane_and_borders() -> None:
    columns = [
        _Column(key="group", headerPath=["Продукция"], horizontalAlignment="left", verticalAlignment="center"),
        _Column(key="label", headerPath=["Реализация", "Объем"], horizontalAlignment="right", verticalAlignment="top"),
    ]
    sink = BytesIO()
    writer = TableWorkbookWriter(sink, "Лист", columns, header_pinned=True, row_total=2, merge_rows_by=("group",))
    writer.write_rows([_Row(group="A", label="1"), _Row(group="A", label="2")])
    writer.close()
    sheet = load_workbook(BytesIO(sink.getvalue())).worksheets[0]
    assert sheet.freeze_panes == "A3"
    assert sheet["A3"].alignment.horizontal == "left"
    assert sheet["A3"].alignment.vertical == "center"
    assert sheet["B3"].alignment.horizontal == "right"
    assert sheet["B3"].alignment.vertical == "top"
    assert sheet["A1"].alignment.horizontal == "center"
    assert sheet["B2"].border.bottom.style == "thin"
    assert sheet["B2"].border.bottom == sheet["B2"].border.top
    assert sheet.max_column == 2
    with ZipFile(BytesIO(sink.getvalue())) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314 — generated test workbook
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        dimension = root.find("s:dimension", ns)
        assert dimension is not None
        assert dimension.attrib["ref"] == "A1:B4"
        formats = ET.fromstring(archive.read("xl/styles.xml")).find("s:cellXfs", ns)  # noqa: S314
        assert formats is not None
        assert int(formats.attrib["count"]) == len(formats)


def test_alignment_preserves_datetime_and_formula_formats() -> None:
    class Row(BaseModel):
        stamp: datetime
        first: float
        second: float
        difference: float

    columns = [
        _Column(key=key, headerPath=[key], horizontalAlignment="center", verticalAlignment="bottom")
        for key in ("stamp", "first", "second", "difference")
    ]
    sink = BytesIO()
    writer = TableWorkbookWriter(
        sink,
        "Лист",
        columns,
        header_pinned=False,
        row_total=1,
        calculated={"difference": ("first", "second")},
        totals_row={"first": 7},
    )
    writer.write_rows([Row(stamp=datetime(2026, 10, 21), first=7, second=2, difference=5)])  # noqa: DTZ001
    writer.close()
    sheet = load_workbook(BytesIO(sink.getvalue())).worksheets[0]
    assert sheet["A3"].number_format == "DD.MM.YYYY HH:MM:SS"
    assert sheet["D3"].value == "=B3-C3"
    assert sheet["B2"].value == "=SUM(B3:B3)"
    for ref in ("A3", "D3", "B2"):
        assert sheet[ref].alignment.horizontal == "center"
        assert sheet[ref].alignment.vertical == "bottom"
