"""Correctness checks for the XLSX export size benchmark harness itself."""

from tests.benchmark_xlsx_export_size import (
    Workload,
    _scaled,
    _stats,
    catalog,
    inspect,
    render,
    run_one,
)


def _by_id(name: str) -> Workload:
    return next(item for item in catalog() if item.id == name)


def test_catalog_covers_required_shapes() -> None:
    items = catalog()
    profiles = {item.profile for item in items}
    assert {"typical", "repeated", "unique_long", "sparse", "nested"} <= profiles
    assert {item.family for item in items} == {"plan", "table"}
    assert any(item.values_only for item in items)
    assert any(not item.values_only for item in items)
    assert any(item.block for item in items)
    assert _by_id("plan_large_values").rows > _by_id("plan_typical_values").rows
    assert _by_id("table_large_values").rows > _by_id("table_small_values").rows
    assert _scaled(_by_id("plan_typical_values"), 0.5).rows == 500


def test_render_is_deterministic() -> None:
    for name in ("plan_small_values", "table_small_formulas"):
        workload = _by_id(name)
        assert render(workload) == render(workload)


def test_plan_inspection_separates_real_and_empty_cells() -> None:
    result = run_one(_by_id("plan_small_values"), reps=1)
    sheet = result["inspection"]["sheets"]["xl/worksheets/sheet1.xml"]
    assert sheet["rows_serialized"] == 13 + 20
    assert sheet["cells_serialized"] > sheet["non_empty_cells"]
    assert sheet["styled_empty_cells"] > 0
    assert not sheet["dimension_inflated"]
    assert result["formula_count"] == 0
    assert result["sheet_count"] == 1
    assert result["inspection"]["shared_strings"]["present"] is False


def test_table_inspection_counts_features_and_formulas() -> None:
    result = run_one(_by_id("table_nested_formulas"), reps=1)
    inspection = result["inspection"]
    sheet = inspection["sheets"]["xl/worksheets/sheet1.xml"]
    assert result["sheet_count"] == 2, "the hidden lists sheet must be present"
    assert sheet["validation_count"] > 0
    assert sheet["merge_range_count"] > 0
    assert sheet["frozen_pane"] is not None
    assert result["formula_count"] >= 2, "calculated cells plus the pinned totals row"
    assert inspection["styles"]["cellXfs"] >= 10, "the shared 10-style catalogue must be present"
    assert sheet["non_empty_cells"] <= sheet["cells_serialized"]


def test_inspect_accepts_a_generated_archive() -> None:
    workload = _by_id("table_small_values")
    data = render(workload)
    result = inspect(data, header_rows_count=2, main_sheet="xl/worksheets/sheet1.xml")
    assert result["final_bytes"] == len(data)
    assert result["zip"]["uncompressed_bytes_sum"] > result["zip"]["compressed_bytes_sum"]
    assert result["zip"]["entries"][0]["compressed_bytes"] >= result["zip"]["entries"][-1]["compressed_bytes"]
    ranked = result["ranked_contributors"]
    assert sum(item["uncompressed_bytes"] for item in ranked) == result["zip"]["uncompressed_bytes_sum"]


def test_stats_reports_median_and_range() -> None:
    assert _stats([2, 4, 6]) == {"n": 3, "median": 4, "min": 2, "max": 6}
