"""Loading a validated plan file into the working renovation plan."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation.import_apply import _reference_ids, _resolve_reference, apply_plan_file
from src.renovation.import_rows import ParsedPlanRow
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState

_SAMPLE = next(Path(__file__).resolve().parents[2].joinpath("docs").glob("*.xlsb"), None)


async def test_a_numeric_option_resolves_to_its_catalog_id(db_session: AsyncSession) -> None:
    """Excel sends criticality as «3.0»; the catalog label is «3»."""
    reference_ids = await _reference_ids(db_session)
    resolved = _resolve_reference("col_015", "3.0", reference_ids)
    assert resolved != "3.0"
    assert resolved == _resolve_reference("col_015", "3", reference_ids)


async def test_a_label_the_catalog_does_not_know_stays_as_text(db_session: AsyncSession) -> None:
    """Strict lookup: «Собственными силами» is not «Собственные силы»."""
    assert (
        _resolve_reference("col_020", "Собственными силами", await _reference_ids(db_session)) == "Собственными силами"
    )


async def test_an_option_added_to_the_dictionary_after_the_seed_is_loadable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The load resolves against the dictionary the operator edits, not the seed.

    «Собственными силами» is the wording the technical-directorate file uses.
    An operator adds it to «Способ выполнения работ» and the preflight (which
    already reads the live catalog) says «ready» — but the load looked the label
    up in the frozen seed list and refused the WHOLE file with «значение не
    найдено в справочнике». Both passes must read the same dictionary.
    """
    option = RenovationReferenceValue(catalog="execution_method", label="Собственными силами")
    db_session.add(option)
    await db_session.flush()

    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_LIVE_DICTIONARY",
            asset_code="BSZR",
            values={
                "col_015": "3",
                "col_017": "ТОиР",
                "col_018": "КР",
                "col_019": "OPEX",
                "col_020": "Собственными силами",
                "col_005": "Загруженная работа",
            },
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source: rows)

    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (1, 0)

    loaded = (
        await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.col_003) == "TEST_LIVE_DICTIONARY"))
    ).one()
    assert loaded.col_020 == option.id


async def test_the_sample_file_is_refused_while_it_carries_unknown_catalog_values(
    db_session: AsyncSession,
) -> None:
    """
    A value outside the reference catalog aborts the whole load.

    The technical-directorate sample names «ОКР», «ДО» and «Собственными
    силами», none of which is a catalog option: the preflight reports them as
    errors and the load refuses the file instead of writing it half-resolved.
    No row may be created or changed by that attempt.
    """
    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    before = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )

    with pytest.raises(ValueError, match=r"2027_BSZR_1\.3\.1_0706179"), _SAMPLE.open("rb") as source:
        await apply_plan_file(db_session, source=source, user_id=None)

    after = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    assert after == before


async def test_an_asset_outside_the_dictionary_refuses_the_whole_file(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The asset is a reference value: an unknown one aborts the load.

    A code the project's dictionary does not have must not be silently dropped
    from the plan — the preflight reports it and the load refuses the file, so
    the plan stays exactly as it was.
    """
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    before = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    rows = [
        # A known asset first: the refusal must undo what the batch would write.
        ParsedPlanRow(code="TEST_ASSET_KNOWN", asset_code="BSZR", values={"col_005": "известный актив"}),
        ParsedPlanRow(code="TEST_ASSET_UNKNOWN", asset_code="ZZZR", values={"col_005": "неизвестный актив"}),
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source: rows)

    with pytest.raises(ValueError, match="TEST_ASSET_UNKNOWN"):
        await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]

    after = (
        await db_session.exec(select(col(RenovationPlanRow.col_003)).where(col(RenovationPlanRow.plan_id) == plan.id))
    ).all()
    assert len(after) == before
    assert "TEST_ASSET_KNOWN" not in after


async def test_a_clean_file_creates_rows_once_and_updates_them_next_time(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Codes new to the plan append rows; the same file again updates in place.

    The parse step is stubbed so the test owns the labels (a real sheet would
    need the very catalog values the strict load rejects). Everything after the
    parse ? reference resolution, batching, recompute ? runs for real.
    """
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    before = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    rows = [
        ParsedPlanRow(
            code=f"TEST_IMPORT_{index}",
            asset_code="BSZR",
            values={
                "col_015": "3",
                "col_017": "ТОиР",
                "col_018": "КР",
                "col_019": "OPEX",
                "col_020": "Собственные силы",
                "col_005": f"Загруженная работа {index}",
            },
        )
        for index in range(1, 4)
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source: rows)

    first = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (first.new_rows, first.updated_rows) == (3, 0)
    after_first = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    assert after_first == before + 3

    loaded = (
        await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.col_003) == "TEST_IMPORT_2"))
    ).one()
    assert str(loaded.col_015) == _resolve_reference("col_015", "3", await _reference_ids(db_session))
    assert loaded.position > 0

    second = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (second.new_rows, second.updated_rows) == (0, 3)
    after_second = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    assert after_second == after_first


async def test_the_load_reports_write_and_recompute_progress(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`expected_rows` sizes a two-phase bar: the write, then the derived values."""
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(code=f"TEST_PROGRESS_{index}", asset_code="BSZR", values={"col_005": f"Работа {index}"})
        for index in range(1, 4)
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source: rows)
    events: list[tuple[int, int, str]] = []

    await apply_plan_file(
        db_session,
        source=None,  # ty: ignore[invalid-argument-type]
        user_id=None,
        expected_rows=3,
        progress=lambda done, total, phase: events.append((done, total, phase)),
    )

    # Write + recompute: the preflight's 3 rows become a total of 6 units.
    assert events[0] == (0, 6, "parse")
    assert (3, 6, "write") in events
    assert events[-1] == (6, 6, "recompute")
    assert {phase for _, _, phase in events} == {"parse", "write", "recompute"}
    assert [done for done, _, _ in events] == sorted(done for done, _, _ in events)


async def test_a_ragged_batch_loads_and_its_dates_are_stored_as_dates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Blank cells are dropped by the parser, so a real batch is ragged.

    A multi-row INSERT renders ONE `VALUES` clause, which demands identical
    keys per row — a batch where one row lacks a cell raised «INSERT value for
    column r_plan_rows.col_011 is explicitly rendered as a boundparameter» and
    the whole file was discarded. The write pads every row to the batch's key
    union, so a cell the file omitted becomes NULL.

    Dates are the parser's ISO text (its contract — the same value feeds the
    renovation-ID rule) while the columns are DATE; asyncpg rejects a string
    with «'str' object has no attribute 'toordinal'», so the write converts.
    """
    if (await db_session.exec(select(RAsset).where(col(RAsset.repair_code) == "BSZR"))).first() is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_RAGGED_1",
            asset_code="BSZR",
            values={
                "col_015": "3",
                "col_017": "ТОиР",
                "col_018": "КР",
                "col_019": "OPEX",
                "col_020": "Собственные силы",
                "col_005": "Работа с датой",
                "col_024": "2027-10-01",
            },
        ),
        ParsedPlanRow(
            code="TEST_RAGGED_2",
            asset_code="BSZR",
            values={
                "col_015": "3",
                "col_017": "ТОиР",
                "col_018": "КР",
                "col_019": "OPEX",
                "col_020": "Собственные силы",
                "col_005": "Работа без даты",
                # col_024 deliberately absent: the parser skips a blank cell,
                # so these two rows of one batch have different key sets.
            },
        ),
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source: rows)

    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (2, 0)

    loaded = {
        row.col_003: row
        for row in (
            await db_session.exec(
                select(RenovationPlanRow).where(col(RenovationPlanRow.col_003).in_(["TEST_RAGGED_1", "TEST_RAGGED_2"]))
            )
        ).all()
    }
    assert set(loaded) == {"TEST_RAGGED_1", "TEST_RAGGED_2"}
    assert loaded["TEST_RAGGED_1"].col_024 == date(2027, 10, 1)
    # The omitted cell is NULL, not a neighbour's value.
    assert loaded["TEST_RAGGED_2"].col_024 is None
    assert loaded["TEST_RAGGED_1"].col_005 == "Работа с датой"
    assert loaded["TEST_RAGGED_2"].col_005 == "Работа без даты"
