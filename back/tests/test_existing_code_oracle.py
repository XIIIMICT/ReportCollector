"""
Bounded existing-code oracle: exact membership, cleanup and a 300k-plan comparison.

Methodology for the large test (no production data, no DB, one process):
1. A generator yields 150k deterministic existing work codes (the "working
   plan") and a second generator yields 300k valid plan rows whose codes mix
   twice-repeated existing codes with brand-new ones. Nothing is materialized
   as a workbook or a Python list of rows.
2. The new path feeds the plan generator into `ExistingCodeOracle`, seals it,
   and inspects the row generator through the real analyzer. `tracemalloc`
   starts before the oracle is built and stops after the report is returned,
   so its peak covers BOTH the index build and the parse.
3. The old path repeats the identical rows with the original
   `existing_codes=set(...)` representation. Both reports must be exactly
   equal (totals, duplicate examples, status) and the traced peak of the
   bounded path must be far smaller.
4. RSS is sampled before/after each path with `current_rss_bytes()` and
   reported, not asserted: a process-wide working set is noisier than the
   traced Python heap and includes the OS page cache behind the mmap.
5. An untraced build + 300k-membership pass reports realistic wall time:
   tracing millions of tiny mmap slices inflates the bounded path's traced
   wall time far more than the set's in-C hash lookups.

Run with `uv run --directory back pytest -q tests/test_existing_code_oracle.py -s`
to see the measured numbers.
"""

import asyncio
import contextlib
import time
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation import import_plan, router_import
from src.renovation.import_plan import (
    CodeMembership,
    ExistingCodeOracle,
    inspect_plan_file_streaming,
)
from src.renovation.inspect_cache import InspectResultCache
from src.renovation.plan import RenovationPlan, RenovationPlanRow
from tests.benchmark_import_bounded_memory import current_rss_bytes

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from src.renovation.schemas import RenovationPlanImportResult

_PLAN_CODES = 150_000
_FILE_ROWS = 300_000


def _columns() -> list[object]:
    meta = next(meta for meta in import_plan.field_metas() if meta.db_name == "work_cost_rub_ex_vat")
    return [
        import_plan._ASSET_HEADER,
        import_plan._CODE_HEADER,
        *(label for _, label in import_plan._REQUIRED_COLUMN_LABELS),
        meta.label,
    ]


_REFERENCES = {
    "criticality": {"1"},
    "work_category": {"тоир"},
    "work_type": {"кр"},
    "cost_allocation": {"opex"},
    "execution_method": {"собственные силы"},
}


def _plan_code_stream() -> Iterator[str]:
    for number in range(_PLAN_CODES):
        yield f"PLAN_{number:06d}"


def _file_rows() -> Iterator[list[object]]:
    yield _columns()
    for number in range(_FILE_ROWS):
        code = f"PLAN_{number % 100_000:06d}" if number < 200_000 else f"NEW_{number - 200_000:06d}"
        yield [
            "BSZR",
            code,
            "1",
            "ТОиР",
            "КР",
            "OPEX",
            "Собственные силы",
            "2027-01-01",
            "2027-01-31",
            "1",
        ]


def test_membership_is_exact_for_edge_codes() -> None:
    """Unicode, whitespace, prefixes and blanks behave exactly like a set."""
    codes = ["", "PLAIN", "plain", " КОД ", "КОД", "A" * 500, "line\nbreak", "0", "PLAIN "]
    expected = {code for code in codes if code}
    with ExistingCodeOracle() as oracle:
        for code in codes:
            oracle.add(code)
        oracle.seal()
        assert len(oracle) == len(expected)
        assert oracle.added == len(expected)
        for code in [*codes, "ABSENT", "Plain", "КОД "]:
            assert oracle.contains(code) is (code in expected)
        assert "PLAIN" in oracle
        assert "ABSENT" not in oracle


def test_external_sort_merges_runs_and_counts_distinct_codes() -> None:
    """More than one sorted run, duplicate codes and a blank are handled exactly."""
    unique = [f"CODE_{number:06d}" for number in range(import_plan._ORACLE_RUN_CODES * 2 + 17)]
    fed = [*unique, *unique[:100], ""]
    with ExistingCodeOracle() as oracle:
        for code in fed:
            oracle.add(code)
        assert len(oracle._runs) >= 2
        oracle.seal()
        assert len(oracle) == len(unique)
        assert oracle.added == len(fed) - 1
        assert all(oracle.contains(code) for code in unique)
        assert not oracle.contains("")


def test_oracle_closes_temp_files_on_success_error_and_cancellation() -> None:
    """The context manager releases the mmap and every temporary file on all paths."""
    for failure in (None, ValueError("parse failed"), asyncio.CancelledError()):
        oracle = ExistingCodeOracle()
        oracle.add("CODE")
        oracle.seal()
        handles = list(oracle._files)
        try:
            with oracle:
                if failure is not None:
                    raise failure
        except ValueError, asyncio.CancelledError:
            pass
        assert handles
        assert all(handle.closed for handle in handles)
        assert oracle._map is None
        with pytest.raises(RuntimeError, match="sealed"):
            oracle.contains("CODE")
        oracle.close()  # idempotent


async def test_inspect_closes_the_oracle_when_the_parse_is_cancelled(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled request must not leave disk-backed code indexes behind."""
    del db_session
    from conftest import signed_identity_headers

    created: list[ExistingCodeOracle] = []
    original = ExistingCodeOracle

    def factory() -> ExistingCodeOracle:
        oracle = original()
        created.append(oracle)
        return oracle

    def cancel(*_args: object, **_kwargs: object) -> RenovationPlanImportResult:
        msg = "parse cancelled"
        raise asyncio.CancelledError(msg)

    monkeypatch.setattr(router_import, "ExistingCodeOracle", factory)
    monkeypatch.setattr(router_import, "get_inspect_cache", lambda: InspectResultCache(None))
    monkeypatch.setattr(
        router_import,
        "scan_file_scope",
        lambda *_args, **_kwargs: import_plan.PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset()),
    )
    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", cancel)
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await client.post(
            "/api/renovation/plan/import/inspect",
            files={"file": ("plan.xlsb", b"opaque bytes", "application/octet-stream")},
            headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
        )
    assert len(created) == 1
    assert created[0]._closed


def test_large_plan_preflight_keeps_exact_totals_with_a_bounded_heap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """150k-code plan × 300k-row workbook: identical reports, much smaller heap."""
    monkeypatch.setattr(import_plan, "_iter_plan_rows", lambda *_args, **_kwargs: _file_rows())

    def inspect(existing_codes: object) -> RenovationPlanImportResult:
        return inspect_plan_file_streaming(
            Path("synthetic_plan.xlsx"),
            existing_codes=cast("CodeMembership | set[str]", existing_codes),
            asset_names_by_code={"BSZR": "АО «БСЗ»"},
            reference_values=_REFERENCES,
            suffix=".xlsx",
            issue_limit=10,
        )

    tracemalloc.start()
    rss_before_oracle = current_rss_bytes()
    started = time.perf_counter()
    oracle = ExistingCodeOracle()
    for code in _plan_code_stream():
        oracle.add(code)
    oracle.seal()
    bounded_result = inspect(oracle)
    bounded_wall = time.perf_counter() - started
    _, bounded_peak = tracemalloc.get_traced_memory()
    rss_after_oracle = current_rss_bytes()
    tracemalloc.stop()
    oracle.close()

    tracemalloc.start()
    rss_before_set = current_rss_bytes()
    started = time.perf_counter()
    codes = set(_plan_code_stream())
    set_result = inspect(codes)
    set_wall = time.perf_counter() - started
    _, set_peak = tracemalloc.get_traced_memory()
    rss_after_set = current_rss_bytes()
    tracemalloc.stop()
    del codes

    # Untraced wall time: tracing millions of tiny mmap slices inflates the
    # bounded path far more than the set's in-C hash lookups, so the traced
    # wall times above are a measurement artifact, not production latency.
    started = time.perf_counter()
    plain_oracle = ExistingCodeOracle()
    for code in _plan_code_stream():
        plain_oracle.add(code)
    plain_oracle.seal()
    build_wall = time.perf_counter() - started
    started = time.perf_counter()
    hits = sum(plain_oracle.contains(f"PLAN_{number % 100_000:06d}") for number in range(_FILE_ROWS))
    lookup_wall = time.perf_counter() - started
    plain_oracle.close()
    assert hits == _FILE_ROWS

    # Exact semantics: the workbook repeats each of the first 100k plan codes
    # twice, then adds 100k codes that exist in no plan version.
    for result in (bounded_result, set_result):
        assert result.status == "has_errors"  # the deliberate whole-file duplicates
        assert result.new_rows == 100_000
        assert result.rows_to_update == 200_000
        assert result.issue_counts["duplicate_codes"] == 100_000
        assert len(result.duplicate_codes) == 10
        assert result.issue_counts["invalid_data_types"] == 0
        assert result.issue_counts["missing_required_values"] == 0
        assert result.issue_counts["unknown_assets"] == 0
    assert bounded_result == set_result

    def mib(delta: int | None) -> str:
        return "n/a" if delta is None else f"{delta / 2**20:.1f}"

    print(  # noqa: T201 - numbers are explained in the module docstring
        "\n300k-plan preflight (synthetic, one developer machine):\n"
        f"  bounded oracle: {bounded_wall:.1f}s, traced peak {bounded_peak / 2**20:.1f} MiB, "
        f"RSS delta {mib(None if rss_after_oracle is None or rss_before_oracle is None else rss_after_oracle - rss_before_oracle)} MiB\n"
        f"  old Python set: {set_wall:.1f}s, traced peak {set_peak / 2**20:.1f} MiB, "
        f"RSS delta {mib(None if rss_after_set is None or rss_before_set is None else rss_after_set - rss_before_set)} MiB\n"
        f"  untraced oracle: build {build_wall:.1f}s + 300k membership tests {lookup_wall:.1f}s"
    )
    assert bounded_peak < 10 * 1024 * 1024
    assert set_peak > 12 * 1024 * 1024
    assert bounded_peak < set_peak / 2


async def test_router_builds_the_oracle_only_from_the_working_plan(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DB page reader feeds the oracle with working-plan codes only."""
    del monkeypatch
    plan = (await db_session.exec(select(RenovationPlan))).first()
    assert plan is not None
    oracle = ExistingCodeOracle()
    await router_import._fill_existing_code_oracle(db_session, oracle, plan.id)
    oracle.seal()
    rows = (
        await db_session.exec(
            select(col(RenovationPlanRow.work_or_repair_code)).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).all()
    expected = {code for code in rows if code}
    assert len(oracle) == len(expected)
    assert all(oracle.contains(code) for code in expected)
    oracle.close()
