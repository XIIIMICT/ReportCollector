from io import BytesIO

from openpyxl import load_workbook
from pydantic import BaseModel

from src.common.xlsx_table import TableWorkbookWriter


class _Column(BaseModel):
    """Minimal export column (the writer's ``ExportColumnLike`` protocol)."""

    key: str
    headerPath: list[str]
    width: float = 20.0
    numberFormat: str | None = None


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
