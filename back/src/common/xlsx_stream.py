"""
Streaming XLSX (raw OOXML) writer mechanism, project-agnostic.

A project's export defines its LAYOUT (columns, header, styles, formulas);
this module owns the generic mechanics of the package so every project shares
one implementation:

- the static parts of a minimal workbook (content types, rels, a default
  stylesheet) written once per archive;
- the sheet prefix with an optional frozen pane;
- XML cell builders (inline strings, numbers, formulas with a cached value);
- the Excel date serial conversion;
- the ``<mergeCells>`` builder, which deduplicates refs.

Two Excel strictness rules motivated the last two items: a workbook whose
``<row>`` elements are out of `r` order (e.g. an aggregating row emitted after
the data rows) is rejected with the repair dialog, and so is a duplicated
``<mergeCell ref>``. Callers must therefore emit rows in ascending order, and
the merge builder refuses duplicates automatically.

Strings are written inline (no sharedStrings part) to keep memory bounded for
plans of hundreds of thousands of rows.
"""

import re
import zipfile
from collections.abc import Iterable, Sequence
from datetime import date
from io import UnsupportedOperation
from typing import IO
from xml.sax.saxutils import escape

# CPython 3.14 lowered zipfile's plain-entry threshold to 2 GiB
# (``ZIP64_LIMIT = (1 << 31) - 1``) out of caution for signed-32-bit readers.
# The streamed plain-ZIP format actually carries 4 GiB - 1, and a streamed
# zip64 entry's zeroed local-header extra is rejected by real Excel (COM-
# verified), so the append-only exports keep plain entries and restore the
# format's true ceiling. Real plan sheet: ~3.3 GiB — fits with headroom.
zipfile.ZIP64_LIMIT = max(zipfile.ZIP64_LIMIT, (1 << 32) - 1)

# Deflate level for every streamed XLSX entry. Level 1 was the original
# default; measured on the full 18-workload matrix it left 35–43% of the file
# on the table versus level 6, whose compression CPU is only ~2x level 1
# (<1 s even for the 18 MB / 90 MB-uncompressed plan fixture). Level 9 buys a
# further 4–7 percentage points but costs 3–7x the CPU, so 6 is the default.
# Both writers expose it as a constructor argument for a documented override.
ZIP_COMPRESSLEVEL = 6

from openpyxl.utils import get_column_letter

# Characters XML 1.0 cannot represent even escaped; Excel treats the whole
# part as invalid (usually a stray control character from stored text).
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

CONTENT_TYPES_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{sheets}'
    '<Override PartName="/xl/styles.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'
)
ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""


def content_types_xml(sheet_count: int = 1) -> str:
    """Return the ``[Content_Types].xml`` part for one or more worksheets."""
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml"'
        f' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return CONTENT_TYPES_TEMPLATE.format(sheets=overrides)


def workbook_rels_xml(sheet_count: int = 1) -> str:
    """Return ``xl/_rels/workbook.xml.rels``: worksheet parts, then styles."""
    relations = "".join(
        f'<Relationship Id="rId{index}"'
        f' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        f' Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    relations += (
        f'<Relationship Id="rId{sheet_count + 1}"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
        ' Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{relations}</Relationships>"
    )


# Shared stylesheet. Style ids referenced from cell XML:
# 0 plain, 1 bold bordered header (wrapped), 2 date `DD.MM.YYYY`,
# 3 decimal `0.00`, 4 bold bordered, 5 bold bordered centered,
# 6 bold decimal bordered, 7 bold bordered decimal (totals),
# 8 plain bordered data cell, 9 plain bordered datetime `DD.MM.YYYY HH:MM:SS`
# (the table exporter's data rows; 9 keeps the shared datetime format).
STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="3"><numFmt numFmtId="164" formatCode="DD.MM.YYYY"/><numFmt numFmtId="165" formatCode="0.00"/><numFmt numFmtId="166" formatCode="DD.MM.YYYY HH:MM:SS"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="10"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/><xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="165" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyNumberFormat="1"/><xf numFmtId="165" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyNumberFormat="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/><xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""


def workbook_xml(sheets: Sequence[tuple[str, bool]] | str) -> str:
    """
    Return the ``xl/workbook.xml`` part.

    ``sheets`` is either a single sheet name (the common one-sheet case) or a
    sequence of ``(name, hidden)`` pairs — a hidden second sheet carries the
    dropdown reference lists of dictionary exports.
    """
    if isinstance(sheets, str):
        sheets = [(sheets, False)]
    entries = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}"'
        + (' state="hidden"' if hidden else "")
        + f' r:id="rId{index}"/>'
        for index, (name, hidden) in enumerate(sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{entries}</sheets>"
        '<calcPr calcId="0" fullCalcOnLoad="1"/></workbook>'
    )


def sheet_prefix_xml(x_split: int = 0, y_split: int = 0, dimension: str | None = None) -> str:
    """
    Open the worksheet: root, optional dimension, sheet views, row format.

    ``topLeftCell`` follows the splits automatically (C14 for x=2, y=13), which
    keeps the frozen pane pointing at the first scrollable cell. ``dimension``
    (e.g. ``A1:F5``) is optional but wanted: openpyxl in read-only mode
    derives ``max_row``/``max_column`` from it — without the element readers
    see ``None``.
    """
    dimension_xml = f'<dimension ref="{dimension}"/>' if dimension else ""
    views = '<sheetView workbookViewId="0">'
    if x_split or y_split:
        active_pane = "topLeft"
        if x_split and y_split:
            active_pane = "bottomRight"
        elif x_split:
            active_pane = "topRight"
        elif y_split:
            active_pane = "bottomLeft"
        top_left = f"{get_column_letter(x_split + 1)}{y_split + 1}"
        # Canonical attribute order as Excel itself writes it.
        attrs = []
        if x_split:
            attrs.append(f'xSplit="{x_split}"')
        if y_split:
            attrs.append(f'ySplit="{y_split}"')
        attrs.append(f'topLeftCell="{top_left}"')
        attrs.append(f'activePane="{active_pane}"')
        attrs.append('state="frozen"')
        views += f"<pane {' '.join(attrs)}/>"
    views += "</sheetView>"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{dimension_xml}<sheetViews>{views}</sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
    )


def xml_escape(value: str) -> str:
    """Escape XML specials and drop characters XML cannot represent."""
    return escape(_ILLEGAL_XML_CHARS.sub("", value))


class UnseekableSink:
    """
    Wrap a real file so ``zipfile`` streams append-only (data descriptors).

    ``ZipFile`` on a seekable sink writes a draft local header (zero CRC and
    sizes) and patches it by seeking back at ``close()``. Anything tailing the
    file while it grows — the export job's download streamer — receives the
    draft bytes and can never see the patch, so the downloaded archive carries
    an invalid local header (Excel repair-dialogs it). A sink whose ``seek``
    raises makes ``zipfile`` set the data-descriptor flag instead: the header
    honestly says "sizes follow the data" and every byte is written exactly
    once, in order.
    """

    def __init__(self, sink: IO[bytes]) -> None:
        self._sink = sink

    def write(self, data: bytes) -> int:
        return self._sink.write(data)

    def flush(self) -> None:
        self._sink.flush()

    def tell(self) -> int:
        return self._sink.tell()

    def seek(self, *_args: object) -> int:
        raise UnsupportedOperation


def date_serial(value: date) -> int:
    """Excel date serial for a plain date (the 1899-12-30 epoch)."""
    return (value - date(1899, 12, 30)).days


def text_cell_xml(ref: str, value: str, *, style: int = 0) -> str:
    """Return an inline-string cell (no sharedStrings part)."""
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{xml_escape(value)}</t></is></c>'


def number_cell_xml(ref: str, value: object, *, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'


def styled_cell_xml(ref: str, *, style: int = 0) -> str:
    """Return an empty styled cell (e.g. a bordered placeholder inside a merge)."""
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}/>'


def formula_cell_xml(ref: str, formula: str, *, style: int = 0, cached: float | None = None) -> str:
    """
    Return a cell with an Excel formula plus an optional cached value.

    The formula text must be pre-escaped (the substitutions in a template are
    already XML-safe) and NOT start with ``=`` (the `<f>` content is the
    formula itself).
    """
    style_attr = f' s="{style}"' if style else ""
    cached_xml = f"<v>{cached}</v>" if cached is not None else ""
    return f'<c r="{ref}"{style_attr}><f>{formula}</f>{cached_xml}</c>'


def merge_cells_xml(range_refs: Iterable[str]) -> str:
    """
    Return the ``<mergeCells>`` block, WITHOUT duplicates — or an empty string.

    Excel rejects a workbook that lists the same range twice (repair dialog),
    so duplicates are dropped preserving the first occurrence. Intersecting
    (not identical) ranges are also forbidden by Excel, but ours cannot
    overlap by construction: header merges cover distinct template cells and
    the aggregating-band merges are per-column. A table without merges must
    not emit the block at all — a bare ``<mergeCells count="0"/>`` is another
    repair-dialog trigger.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for ref in range_refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    if not unique:
        return ""
    refs = "".join(f'<mergeCell ref="{cell_range}"/>' for cell_range in unique)
    return f'<mergeCells count="{len(unique)}">{refs}</mergeCells>'


def bool_cell_xml(ref: str, value: bool, *, style: int = 0) -> str:
    """Return a boolean cell (Excel stores booleans as `t="b"` with 1/0)."""
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'


def data_validations_xml(validations: Iterable[tuple[str, str]]) -> str:
    """
    Return the ``<dataValidations>`` block: a list-typed dropdown per entry.

    Each entry is ``(sqref, formula1)``; the formula references the hidden
    lists sheet (``=Списки!$B$1:$B$2`` — with the leading ``=``, exactly as
    the previous openpyxl renderer wrote it and as the reader tests pin it).
    Schema-wise the block follows ``<mergeCells>``.
    """
    items = list(validations)
    if not items:
        return ""
    blocks = "".join(
        f'<dataValidation type="list" allowBlank="1" sqref="{sqref}">'
        f"<formula1>{xml_escape(formula)}</formula1></dataValidation>"
        for sqref, formula in items
    )
    return f'<dataValidations count="{len(items)}">{blocks}</dataValidations>'
