"""
Pin the XLSX size optimization and the hypotheses rejected by measurement.

Only the ZIP deflate level changed (1 -> 6, overridable per writer). The tests
below pin that this changes no workbook byte inside the package, that the
append-only/data-descriptor ZIP contract survives, and that the rejected
hypotheses (styled blanks, default-style attributes, shared strings, formula
removal) remain unsupported by the writers' output.
"""

import re
import zipfile
from collections.abc import Sequence
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from openpyxl import load_workbook

from src.common import xlsx_stream
from tests.benchmark_xlsx_export_size import (
    Workload,
    catalog,
    recompress,
    render,
    same_package_parts,
)

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"m": _SHEET_NS}
_M = f"{{{_SHEET_NS}}}"


def _by_id(name: str) -> Workload:
    return next(item for item in catalog() if item.id == name)


def _archive(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(data))


def _style_signatures(data: bytes) -> list[tuple[object, ...]]:
    """Canonical XF signatures: cell `s` ids are replaced by their definition."""
    with _archive(data) as archive:
        root = ET.fromstring(archive.read("xl/styles.xml"))  # noqa: S314 — generated workbook
    xfs = root.find("m:cellXfs", _NS)
    assert xfs is not None
    return [
        (
            tuple(sorted(xf.attrib.items())),
            tuple((child.tag, tuple(sorted(child.attrib.items()))) for child in xf if isinstance(child.tag, str)),
        )
        for xf in xfs
    ]


def _cell_semantics(cell: ET.Element, styles: Sequence[tuple[object, ...]]) -> tuple[object, ...]:
    raw_style = cell.attrib.get("s")
    style = styles[int(raw_style)] if raw_style is not None else styles[0]
    children = tuple(
        (child.tag, tuple(sorted(child.attrib.items())), child.text or "")
        for child in cell
        if isinstance(child.tag, str)
    )
    return cell.attrib.get("r"), cell.attrib.get("t"), style, children


def _sheet_semantics(root: ET.Element, styles: Sequence[tuple[object, ...]]) -> dict[str, object]:
    rows = [
        (
            row.attrib.get("r"),
            row.attrib.get("ht"),
            row.attrib.get("customHeight"),
            tuple(_cell_semantics(cell, styles) for cell in row if cell.tag == f"{_M}c"),
        )
        for row in root.findall("m:sheetData/m:row", _NS)
    ]
    validations = [
        (
            entry.attrib.get("sqref"),
            entry.attrib.get("type"),
            tuple((child.tag, child.text or "") for child in entry if isinstance(child.tag, str)),
        )
        for entry in root.findall("m:dataValidations/m:dataValidation", _NS)
    ]
    dimension = root.find("m:dimension", _NS)
    pane = root.find("m:sheetViews/m:sheetView/m:pane", _NS)
    return {
        "dimension": dimension.attrib if dimension is not None else None,
        "pane": pane.attrib if pane is not None else None,
        "cols": [tuple(sorted(col.attrib.items())) for col in root.findall("m:cols/m:col", _NS)],
        "rows": rows,
        "merges": sorted(merge.attrib["ref"] for merge in root.findall("m:mergeCells/m:mergeCell", _NS)),
        "validations": validations,
    }


def _semantics(data: bytes) -> dict[str, object]:
    """Workbook semantics independent of ZIP order and numeric style ids."""
    with _archive(data) as archive:
        names = archive.namelist()
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))  # noqa: S314 — generated workbook
        sheets = [
            (entry.attrib.get("name"), entry.attrib.get("sheetId"), entry.attrib.get("state", "visible"))
            for entry in workbook.findall("m:sheets/m:sheet", _NS)
        ]
        styles = _style_signatures(data)
        parts = {
            name: _sheet_semantics(ET.fromstring(archive.read(name)), styles)  # noqa: S314 — generated workbook
            for name in names
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        }
    return {"sheets": sheets, "parts": parts, "styles": styles}


def _openpyxl_features(data: bytes) -> dict[str, Any]:
    """Read the generated workbook with the project's reader, features included."""
    book = load_workbook(BytesIO(data))
    result: dict[str, Any] = {
        "sheetnames": book.sheetnames,
        "states": [sheet.sheet_state for sheet in book.worksheets],
        "sheets": {},
    }
    for sheet in book.worksheets:
        result["sheets"][sheet.title] = {
            "bounds": (sheet.max_row, sheet.max_column),
            "freeze": sheet.freeze_panes,
            "merges": sorted(str(span) for span in sheet.merged_cells.ranges),
            "widths": tuple((key, dim.width) for key, dim in sheet.column_dimensions.items()),
            "validations": tuple(
                (str(entry.sqref), entry.type, entry.formula1) for entry in sheet.data_validations.dataValidation
            ),
            "cells": tuple(
                (
                    cell.coordinate,
                    cell.data_type,
                    cell.value,
                    cell.number_format,
                    cell.font.b,
                    cell.alignment.horizontal,
                    cell.alignment.vertical,
                    cell.border.left.style if cell.border.left else None,
                    cell.border.top.style if cell.border.top else None,
                )
                for row in sheet.iter_rows()
                for cell in row
                if cell.value is not None or cell.has_style
            ),
        }
    return result


def test_default_deflate_level_is_the_measured_sweet_spot():
    """The shared default is level 6 and both real writers honour it."""
    assert xlsx_stream.ZIP_COMPRESSLEVEL == 6
    for workload_id in ("plan_typical_values", "table_typical_values"):
        workload = _by_id(workload_id)
        level1 = render(workload, compresslevel=1)
        default = render(workload)
        expected = recompress(level1, 6)
        assert len(default) == pytest.approx(len(expected), rel=0.01)
        # The percentage is NOT portable: the maintainer's Windows build uses
        # zlib-ng (level 1 is unusually weak, ~36% win), while the Linux CI
        # runner uses stock zlib (level 1 is much stronger, ~19% win). Pin the
        # order and a conservative win instead of a build-specific threshold.
        assert len(default) < len(level1) * 0.9, "level 6 must beat level 1 on every zlib build"


def test_explicit_compresslevel_is_configurable_and_content_neutral():
    """A documented override changes compression only — never the OPC parts."""
    workload = _by_id("plan_small_formulas")
    level1 = render(workload, compresslevel=1)
    level6 = render(workload, compresslevel=6)
    level9 = render(workload, compresslevel=9)
    assert len(level9) < len(level6) < len(level1)
    assert same_package_parts(level1, level6)
    assert same_package_parts(level1, level9)
    workload = _by_id("table_small_values")
    table1 = render(workload, compresslevel=1)
    table6 = render(workload, compresslevel=6)
    assert len(table6) < len(table1)
    assert same_package_parts(table1, table6)


@pytest.mark.parametrize("workload_id", ["plan_small_formulas", "table_nested_formulas"])
def test_sheet_semantics_survive_compression_level(workload_id: str):
    """Values, formulas, styles, merges, pane, widths and hidden sheets are identical."""
    workload = _by_id(workload_id)
    low = render(workload, compresslevel=1)
    high = render(workload, compresslevel=9)
    assert _semantics(low) == _semantics(high)
    assert _openpyxl_features(low) == _openpyxl_features(high)


@pytest.mark.parametrize("workload_id", ["plan_typical_values", "table_typical_values"])
def test_archive_stays_append_only_plain_zip(workload_id: str):
    """Every entry keeps data descriptors; no zip64 or seek-back patching."""
    with _archive(render(_by_id(workload_id))) as archive:
        assert archive.testzip() is None
        for info in archive.infolist():
            assert info.flag_bits & 0x08, f"{info.filename}: data descriptor flag lost"
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert not info.extra, f"{info.filename}: unexpected zip64 extra"
            assert info.extract_version < 45, f"{info.filename}: zip64 entry rejected by Excel"
            assert info.filename in archive.namelist()


def test_every_styled_empty_cell_carries_a_border():
    """The sparse fixtures' blanks are border placeholders, not removable bytes."""
    for workload_id in ("table_sparse", "plan_sparse"):
        data = render(_by_id(workload_id))
        with _archive(data) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314 — generated workbook
            xfs = ET.fromstring(archive.read("xl/styles.xml")).find("m:cellXfs", _NS)  # noqa: S314
            assert xfs is not None
        empties = 0
        for row in sheet.findall("m:sheetData/m:row", _NS):
            for cell in row:
                if cell.tag != f"{_M}c" or len(cell):
                    continue
                empties += 1
                style_id = int(cell.attrib.get("s", "0"))
                assert xfs[style_id].attrib.get("borderId") != "0", "blank lost its visual border"
        assert empties > 0, "the fixture must actually exercise styled blanks"


def test_default_style_attribute_is_already_omitted():
    """Style 0 is the XML default; the builders must not serialize `s="0"`."""
    flag = True
    builders = (
        lambda: xlsx_stream.text_cell_xml("A1", "x"),
        lambda: xlsx_stream.number_cell_xml("A1", 1),
        lambda: xlsx_stream.styled_cell_xml("A1"),
        lambda: xlsx_stream.formula_cell_xml("A1", "1+1"),
        lambda: xlsx_stream.bool_cell_xml("A1", flag),
    )
    for build in builders:
        assert " s=" not in build()
    for workload_id in ("plan_typical_values", "table_typical_values"):
        data = render(_by_id(workload_id))
        with _archive(data) as archive:
            raw = archive.read("xl/worksheets/sheet1.xml")
        assert not re.search(rb'<c[^>]*\bs="0"', raw)
        with _archive(data) as archive:
            assert "xl/sharedStrings.xml" not in archive.namelist(), "strings stay inline (no supported switch)"


def test_formula_bytes_are_the_entire_values_to_formulas_delta():
    """The +74% plan variant is real <f> content, a documented product switch."""
    values = render(_by_id("plan_typical_values"))
    formulas = render(_by_id("plan_typical_formulas"))
    with _archive(values) as archive:
        plain = archive.read("xl/worksheets/sheet1.xml")
    with _archive(formulas) as archive:
        live = archive.read("xl/worksheets/sheet1.xml")
    formula_payload = re.findall(rb"<f>.*?</f>", live)
    assert len(formula_payload) == 133_001
    assert sum(len(item) for item in formula_payload) == len(live) - len(plain)
