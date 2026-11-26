"""Bounded-memory import: exact diagnostics, disk-backed dedup, short DB hold."""

import tracemalloc
from io import BytesIO
from pathlib import Path

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.durable_imports import ImportValidationError
from src.renovation import import_apply, import_plan
from src.renovation.import_rows import ParsedPlanRow
from src.renovation.plan import RenovationPlanRow


def _columns() -> list[str]:
    """Header with every required heading plus one numeric cell we can corrupt."""
    meta = next(meta for meta in import_plan.field_metas() if meta.db_name == "work_cost_rub_ex_vat")
    return [
        import_plan._ASSET_HEADER,
        import_plan._CODE_HEADER,
        *(label for _, label in import_plan._REQUIRED_COLUMN_LABELS),
        meta.label,
    ]


def test_large_invalid_workbook_keeps_exact_counts_and_bounded_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    """5000 bad cells report 5000 issues but retain at most `issue_limit` examples."""

    def rows() -> object:
        yield _columns()
        for number in range(5000):
            yield [
                "BSZR",
                f"BAD_{number:05d}",
                "1",
                "ТОиР",
                "КР",
                "OPEX",
                "Собственные силы",
                "2027-01-01",
                "2027-01-31",
                "не число",
            ]

    monkeypatch.setattr(import_plan, "_read_rows", lambda _source, _suffix=None: rows())
    references = {
        "criticality": {"1"},
        "work_category": {"тоир"},
        "work_type": {"кр"},
        "cost_allocation": {"opex"},
        "execution_method": {"собственные силы"},
    }
    tracemalloc.start()
    try:
        result = import_plan.inspect_plan_file(
            Path("ignored.xlsb"),
            existing_codes=set(),
            asset_names_by_code={"BSZR": "АО «БСЗ»"},
            reference_values=references,
            issue_limit=25,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.issue_counts["invalid_data_types"] == 5000
    assert len(result.invalid_data_types) == 25
    assert result.truncated is True
    assert result.status == "has_errors"
    assert result.new_rows == 5000
    assert result.rows_to_update == 0
    # The whole invalid file must not be retained in Python heap. 4 MB is far
    # above the observed peak (~1 MB) and far below an O(rows) collection.
    assert peak < 4 * 1024 * 1024


def test_duplicates_are_exact_when_codes_are_spread_over_many_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate detection is whole-file even though rows are never all in RAM."""

    def rows() -> object:
        yield _columns()
        for number in range(3000):
            yield [
                "BSZR",
                f"CODE_{number % 1000:04d}",
                "1",
                "ТОиР",
                "КР",
                "OPEX",
                "Собственные силы",
                "2027-01-01",
                "2027-01-31",
                "1",
            ]

    monkeypatch.setattr(import_plan, "_read_rows", lambda _source, _suffix=None: rows())
    result = import_plan.inspect_plan_file(
        Path("ignored.xlsb"),
        existing_codes={"CODE_0000"},
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values=None,
        issue_limit=5,
    )
    assert result.issue_counts["duplicate_codes"] == 1000
    assert len(result.duplicate_codes) == 5
    assert result.rows_to_update == 3
    assert result.new_rows == 2997
    assert result.truncated is True
    assert all("строки" in entry for entry in result.duplicate_codes)


async def test_duplicate_code_split_across_parser_batches_is_rejected(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply re-checks uniqueness across batches with a temp-table primary key."""
    duplicate = ParsedPlanRow(code="SPLIT_ACROSS_BATCHES", asset_key="BSZR", values={"workshop_name": "x"})
    monkeypatch.setattr(
        import_apply,
        "iter_plan_row_batches",
        lambda *_args: iter([[duplicate], [duplicate]]),
    )
    with pytest.raises(ImportValidationError, match="SPLIT_ACROSS_BATCHES"):
        await import_apply.apply_plan_file(db_session, source=BytesIO(), user_id=None, streaming=True)
    written = (
        await db_session.exec(
            select(col(RenovationPlanRow.id)).where(
                col(RenovationPlanRow.work_or_repair_code) == "SPLIT_ACROSS_BATCHES"
            )
        )
    ).all()
    assert written == []


async def test_apply_recompute_reads_bounded_id_batches_not_an_affected_list(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 450-row load replays in 200-row batches and recomputes from temp-table pages."""
    rows = [
        ParsedPlanRow(code=f"BOUNDED_{number:04d}", asset_key="BSZR", values={"workshop_name": str(number)})
        for number in range(450)
    ]
    monkeypatch.setattr(
        import_apply, "iter_plan_row_batches", lambda *_args: iter([rows[:200], rows[200:400], rows[400:]])
    )

    captured: list[dict[str, object]] = []
    original = import_apply.recompute_plan_rows

    async def capture(session: AsyncSession, **kwargs: object) -> object:
        captured.append(kwargs)
        return await original(session, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(import_apply, "recompute_plan_rows", capture)
    result = await import_apply.apply_plan_file(db_session, source=BytesIO(), user_id=None, streaming=True)
    assert (result.new_rows, result.updated_rows) == (450, 0)
    assert len(captured) == 1
    assert captured[0].get("row_ids") is None
    assert captured[0]["id_batches"] is not None
    written = (
        await db_session.exec(
            select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code).like("BOUNDED_%"))
        )
    ).all()
    assert len(written) == 450
