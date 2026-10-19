"""
Streaming table workbooks: the shared raw-XML renderer for grid exports.

Replaces the previous openpyxl-based renderer so EVERY table export — a
20-row dictionary or a growing 100k-row form — runs through one
bounded-memory pipeline built on the ``xlsx_stream`` mechanics:

- the archive is opened once over any binary sink (a response buffer for a
  small table, the export job's ``.part`` file for a big one) and rows are
  appended in batches — memory never grows with the table;
- multi-level ``headerPath`` headers merge exactly like the old renderer
  (a shared prefix merges horizontally, a leaf level merges down), and every
  covered cell carries the header style so Excel borders the whole range;
- calculated columns (the «Сырьё» deviations) keep LIVE formulas with the
  stored value cached;
- dictionary columns get Excel dropdowns fed by a hidden «Списки» sheet;
- datetimes keep the shared ``DD.MM.YYYY HH:MM:SS`` format (aware values
  localized to the server zone — the same serials the old renderer wrote),
  booleans become «Да»/«Нет» text, collections join with «, » — the value
  semantics the reader tests pin.

The append-only container rules of ``xlsx_stream`` apply (data descriptors,
no zip64, the 4 GiB plain-entry ceiling) so the bytes can be tailed by the
download streamer while the file is still being written.

User-facing strings (sheet names, «Да»/«Нет») are Russian by the standard.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import IO, Protocol, cast

from openpyxl.utils import get_column_letter

from src.common import xlsx_stream
from src.common.export import LISTS_SHEET_NAME, ExportColumnLike, ExportTooLargeError

# Shared stylesheet ids (see xlsx_stream.STYLES_XML):
# 1 bold bordered wrapped header, 7 bold bordered decimal (totals),
# 8 plain bordered data cell, 9 plain bordered datetime.
_HEADER_STYLE = 1
_TOTALS_STYLE = 7
_DATA_STYLE = 8
_DATETIME_STYLE = 9


class TableRowLike(Protocol):
    """Anything the writer can read a column value from (a pydantic model)."""

    def model_dump(self) -> dict[str, object]: ...


def datetime_serial(value: datetime) -> float:
    """Return the Excel serial of a datetime (the 1899-12-30 epoch, time as a day fraction)."""
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    epoch = datetime(1899, 12, 30, tzinfo=None)  # noqa: DTZ001 - the Excel epoch is a fixed naive constant
    return (value - epoch).days + (
        value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000
    ) / 86400


class TableWorkbookWriter:
    """Write one data table (plus an optional hidden lists sheet) incrementally."""

    def __init__(
        self,
        sink: IO[bytes],
        sheet_title: str,
        columns: Sequence[ExportColumnLike],
        *,
        header_pinned: bool,
        lists: Mapping[str, Sequence[str]] | None = None,
        column_lists: Mapping[str, str] | None = None,
        calculated: Mapping[str, tuple[str, ...]] | None = None,
        row_total: int | None = None,
        totals_row: Mapping[str, float] | None = None,
    ) -> None:
        """
        Open the archive and write everything except the data rows.

        Package parts, header rows, widths, the frozen pane and the hidden
        «Списки» sheet go in here; the merges and dropdown validations are
        BUILT here but WRITTEN at ``close()``: the worksheet schema puts
        them after ``</sheetData>``, so they can only be appended once the
        last data row is in.

        ``lists``/``column_lists`` mirror the old ``add_reference_sheet`` /
        ``apply_reference_validation`` vocabulary: column key → list name,
        list name → items. ``calculated`` maps a column key to its live
        formula's operand KEYS (the «Сырьё» deviations); a column whose
        operands were dropped falls back to its stored value. ``row_total``
        is the EXPECTED number of data rows — the dropdown ranges need the
        exact last row (a wrong promise only stretches the range).
        ``totals_row`` mirrors the grid's pinned summary row (the renovation
        «Предприятия» dictionary): column key → aggregate. The row sits
        directly under the header, a column WITHOUT an aggregate merges its
        header cell down over it (one tall header cell, exactly like the
        grid), and the aggregating cells carry a live SUM over the data.
        """
        self.columns = list(columns)
        self.calculated = dict(calculated or {})
        self.row_total = row_total
        self.totals_row = dict(totals_row) if totals_row else None
        self._written = 0
        self.header_depth = max(len(column.headerPath) for column in self.columns)
        self._first_data_row = self.header_depth + 1
        if self.totals_row is not None:
            self._first_data_row += 1
        self._column_letters = [get_column_letter(index) for index in range(1, len(self.columns) + 1)]
        self._column_lists = dict(column_lists or {})
        self._list_entries = {name: tuple(items) for name, items in (lists or {}).items()}
        self._list_letters = {name: get_column_letter(i) for i, name in enumerate(lists or {}, start=1)}
        self._needs_lists = lists is not None and any(column.key in self._column_lists for column in self.columns)

        self._archive = zipfile.ZipFile(
            cast("IO[bytes]", xlsx_stream.UnseekableSink(sink)),
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
        )
        sheet_count = 2 if self._needs_lists else 1
        self._archive.writestr("[Content_Types].xml", xlsx_stream.content_types_xml(sheet_count))
        self._archive.writestr("_rels/.rels", xlsx_stream.ROOT_RELS_XML)
        self._archive.writestr(
            "xl/workbook.xml",
            xlsx_stream.workbook_xml(
                [(sheet_title, False)] + ([(LISTS_SHEET_NAME, True)] if self._needs_lists else []),
            ),
        )
        self._archive.writestr("xl/_rels/workbook.xml.rels", xlsx_stream.workbook_rels_xml(sheet_count))
        self._archive.writestr("xl/styles.xml", xlsx_stream.STYLES_XML)
        if self._needs_lists:
            self._archive.writestr("xl/worksheets/sheet2.xml", self._lists_sheet_xml())
        # Main sheet: prefix + cols + sheetData up to and including the header
        # rows; the data rows are appended by write_rows, the tail by close().
        self._sheet = self._archive.open("xl/worksheets/sheet1.xml", "w")
        self._sheet.write(self._sheet_head_xml(header_pinned).encode())

    # ── static parts ────────────────────────────────────────────────────

    def _dimension_ref(self) -> str | None:
        """Return the sheet's used range — openpyxl's read-only mode derives bounds from it."""
        if not self._column_letters or self.row_total is None:
            return None
        last_column = self._column_letters[-1]
        last_row = self._first_data_row + self.row_total - 1
        return f"A1:{last_column}{max(last_row, self.header_depth)}"

    def _sheet_head_xml(self, header_pinned: bool) -> str:
        """Build the worksheet prefix, column widths, the open sheetData, header and totals rows."""
        pinned_depth = self.header_depth + (1 if self.totals_row is not None else 0)
        prefix = xlsx_stream.sheet_prefix_xml(
            y_split=pinned_depth if header_pinned else 0,
            dimension=self._dimension_ref(),
        )
        widths = "".join(
            f'<col min="{index}" max="{index}" width="{min(60.0, max(8.0, column.width))}" customWidth="1"/>'
            for index, column in enumerate(self.columns, start=1)
        )
        # With a totals row, a column WITHOUT an aggregate merges its header
        # cell down OVER the totals row (one tall header cell, exactly like
        # the grid's summary band): the leaf-down merge reaches the totals
        # row instead of the header bottom. An aggregating column keeps a
        # separate summary cell, so its header stops at the header bottom.
        plain_bottom = self.header_depth
        totals_bottom = plain_bottom + 1 if self.totals_row is not None else plain_bottom
        bottom_by_column = [
            plain_bottom if self.totals_row and column.key in self.totals_row else totals_bottom
            for column in self.columns
        ]
        header_rows, self._merges = self._header_rows_xml(bottom_by_column)
        totals_xml = self._totals_row_xml()
        if totals_xml is not None:
            header_rows += totals_xml
        return prefix + f"<cols>{widths}</cols>" + "<sheetData>" + header_rows

    def _header_rows_xml(self, bottom_by_column: Sequence[int]) -> tuple[str, list[str]]:
        """
        Header rows and their merges from the ``headerPath`` parts.

        Mirrors the old openpyxl renderer: columns sharing a prefix on a
        level merge horizontally, a leaf level merges down to the deepest
        row, and every covered cell carries the header style (Excel then
        borders the whole merged range, not just the anchor).
        ``bottom_by_column`` is the row each column's leaf level merges down
        TO — the header bottom, or one row further (over the totals row) for
        the non-aggregating columns of a table with a pinned summary.
        """
        depth = self.header_depth
        # Label per (level, column); None = a filler cell covered by a merge.
        grid: list[list[str | None]] = [[None] * len(self.columns) for _ in range(depth)]
        merges: list[str] = []
        for start, column in enumerate(self.columns):
            path = column.headerPath
            for level in range(len(path)):
                grid[level][start] = path[level]
            # A leaf level extends down to the column's bottom (the header's
            # deepest row, or the totals row for a column the summary does
            # not touch).
            leaf = len(path) - 1
            bottom = bottom_by_column[start]
            if leaf < bottom - 1:
                letter = self._column_letters[start]
                merges.append(f"{letter}{leaf + 1}:{letter}{bottom}")
        # Horizontal merges: consecutive columns sharing the same path prefix.
        for level in range(depth):
            start = 0
            while start < len(self.columns):
                path = self.columns[start].headerPath
                if level >= len(path) or level + 1 == len(path):
                    start += 1
                    continue
                end = start
                while (
                    end + 1 < len(self.columns)
                    and level < len(self.columns[end + 1].headerPath)
                    and self.columns[end + 1].headerPath[: level + 1] == path[: level + 1]
                ):
                    end += 1
                if end > start:
                    first = self._column_letters[start]
                    last = self._column_letters[end]
                    merges.append(f"{first}{level + 1}:{last}{level + 1}")
                start = end + 1
        rows = []
        for level, values in enumerate(grid, start=1):
            cells = "".join(
                xlsx_stream.styled_cell_xml(f"{letter}{level}", style=_HEADER_STYLE)
                if value is None
                else xlsx_stream.text_cell_xml(f"{letter}{level}", value, style=_HEADER_STYLE)
                for letter, value in zip(self._column_letters, values, strict=True)
            )
            rows.append(f'<row r="{level}" ht="34" customHeight="1">{cells}</row>')
        return "".join(rows), merges

    def _totals_row_xml(self) -> str | None:
        """
        Build the pinned summary row (the grid's ``pinnedTopSource``).

        An aggregating column carries a live `SUM` over the exported data
        with the cached aggregate (bold, bordered); every other column stays
        an empty styled cell — its header cell above merges over it, so the
        column reads as one tall header (exactly the grid's summary band).
        Returns None when the table has no totals.
        """
        if self.totals_row is None:
            return None
        row_number = self.header_depth + 1
        data_from = self._first_data_row
        cells = []
        for index, column in enumerate(self.columns):
            ref = f"{self._column_letters[index]}{row_number}"
            cached = self.totals_row.get(column.key)
            if cached is None:
                cells.append(xlsx_stream.styled_cell_xml(ref, style=_HEADER_STYLE))
                continue
            letter = self._column_letters[index]
            if self.row_total:
                formula = f"SUM({letter}{data_from}:{letter}{data_from + self.row_total - 1})"
                cells.append(xlsx_stream.formula_cell_xml(ref, formula, style=_TOTALS_STYLE, cached=cached))
            else:
                cells.append(xlsx_stream.number_cell_xml(ref, cached, style=_TOTALS_STYLE))
        return f'<row r="{row_number}" ht="22" customHeight="1">{"".join(cells)}</row>'

    def _dropdown_validations(self) -> list[tuple[str, str]]:
        """Dropdown ranges bound to the hidden lists sheet's columns."""
        entries: list[tuple[str, str]] = []
        end_row = self._first_data_row + max(self.row_total or 0, 1) - 1
        for index, column in enumerate(self.columns):
            list_name = self._column_lists.get(column.key)
            if list_name is None:
                continue
            items = self._list_entries.get(list_name, ())
            if not items:
                continue
            letter = self._column_letters[index]
            list_letter = self._list_letters[list_name]
            entries.append(
                (
                    f"{letter}{self._first_data_row}:{letter}{end_row}",
                    f"={LISTS_SHEET_NAME}!${list_letter}$1:${list_letter}${len(items)}",
                )
            )
        return entries

    def _lists_sheet_xml(self) -> str:
        """Build the hidden «Списки» sheet: one column per named list, items top-down."""
        rows: list[str] = []
        height = max((len(items) for items in self._list_entries.values()), default=0)
        for row_index in range(1, height + 1):
            cells = []
            for list_name, items in self._list_entries.items():
                if row_index <= len(items):
                    letter = self._list_letters[list_name]
                    cells.append(xlsx_stream.text_cell_xml(f"{letter}{row_index}", str(items[row_index - 1])))
            rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        return xlsx_stream.sheet_prefix_xml() + "<sheetData>" + "".join(rows) + "</sheetData></worksheet>"

    # ── data rows ───────────────────────────────────────────────────────

    def write_rows(self, rows: Iterable[TableRowLike]) -> None:
        """Write one batch of table rows; call repeatedly for the whole table."""
        for row in rows:
            self._written += 1
            row_number = self._first_data_row + self._written - 1
            cells = [f'<row r="{row_number}" ht="22" customHeight="1">']
            values = row.model_dump()
            for index, column in enumerate(self.columns):
                ref = f"{self._column_letters[index]}{row_number}"
                cells.append(self._data_cell_xml(ref, values.get(column.key), column))
            cells.append("</row>")
            self._sheet.write("".join(cells).encode())

    def _data_cell_xml(self, ref: str, value: object, column: ExportColumnLike) -> str:
        """One data cell with the value semantics of the old renderer."""
        operands = self.calculated.get(column.key)
        if operands is not None:
            formula = self._calculated_formula(ref, operands)
            if formula is not None:
                cached = self._numeric_or_none(value)
                return xlsx_stream.formula_cell_xml(ref, formula, style=_DATA_STYLE, cached=cached)
        if isinstance(value, datetime):
            return xlsx_stream.number_cell_xml(ref, datetime_serial(value), style=_DATETIME_STYLE)
        if isinstance(value, bool):
            return xlsx_stream.text_cell_xml(ref, "Да" if value else "Нет", style=_DATA_STYLE)
        if isinstance(value, int | float):
            return xlsx_stream.number_cell_xml(ref, value, style=_DATA_STYLE)
        if isinstance(value, list | tuple):
            joined = ", ".join(str(item) for item in value if item is not None)
            return xlsx_stream.text_cell_xml(ref, joined, style=_DATA_STYLE)
        if value is not None:
            return xlsx_stream.text_cell_xml(ref, str(value), style=_DATA_STYLE)
        return xlsx_stream.styled_cell_xml(ref, style=_DATA_STYLE)

    @staticmethod
    def _numeric_or_none(value: object) -> float | None:
        """Return the cached value of a calculated cell: numbers pass through, else None."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    def _calculated_formula(self, ref: str, operands: Sequence[str]) -> str | None:
        """``A{row}-B{row}`` from the operand column letters; None if an operand was dropped."""
        row_number = "".join(ch for ch in ref if ch.isdigit())
        letters: list[str] = []
        for operand in operands:
            index = next((i for i, column in enumerate(self.columns) if column.key == operand), None)
            if index is None:
                return None
            letters.append(self._column_letters[index])
        return "-".join(f"{letter}{row_number}" for letter in letters)

    def close(self) -> None:
        """
        Finalize the sheet and the archive.

        The merges and the dropdown validations are written here — after
        ``</sheetData>``, where the worksheet schema requires them — and the
        promised ``row_total`` pins the validation ranges. Raises
        ``ExportTooLargeError`` when the sheet outgrew the plain-ZIP ceiling.
        """
        self._sheet.write(
            (
                "</sheetData>"
                + xlsx_stream.merge_cells_xml(self._merges)
                + xlsx_stream.data_validations_xml(self._dropdown_validations())
                + "</worksheet>"
            ).encode()
        )
        try:
            self._sheet.close()
            self._archive.close()
        except RuntimeError as error:
            raise ExportTooLargeError from error

    # Bookkeeping initialized by the constructor helpers.
    _merges: list[str]
