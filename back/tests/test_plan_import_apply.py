"""Loading a validated plan file into the working renovation plan."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
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
    resolved = _resolve_reference("equipment_criticality_group_id", "3.0", reference_ids)
    assert resolved != "3.0"
    assert resolved == _resolve_reference("equipment_criticality_group_id", "3", reference_ids)


async def test_a_label_the_catalog_does_not_know_stays_as_text(db_session: AsyncSession) -> None:
    """Strict lookup: «Собственными силами» is not «Собственные силы»."""
    assert (
        _resolve_reference("execution_method_id", "Собственными силами", await _reference_ids(db_session))
        == "Собственными силами"
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

    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_LIVE_DICTIONARY",
            asset_key="BSZR",
            values={
                "equipment_criticality_group_id": "3",
                "work_category_id": "ТОиР",
                "work_type_id": "КР",
                "cost_allocation_type_id": "OPEX",
                "execution_method_id": "Собственными силами",
                "workshop_name": "Загруженная работа",
            },
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (1, 0)

    loaded = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_LIVE_DICTIONARY")
        )
    ).one()
    assert loaded.execution_method_id == option.id


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
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
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
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
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
        ParsedPlanRow(code="TEST_ASSET_KNOWN", asset_key="BSZR", values={"workshop_name": "известный актив"}),
        ParsedPlanRow(code="TEST_ASSET_UNKNOWN", asset_key="ZZZR", values={"workshop_name": "неизвестный актив"}),
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    with pytest.raises(ValueError, match="TEST_ASSET_UNKNOWN"):
        await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]

    after = (
        await db_session.exec(
            select(col(RenovationPlanRow.work_or_repair_code)).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).all()
    assert len(after) == before
    assert "TEST_ASSET_KNOWN" not in after


async def test_an_asset_named_in_the_file_resolves_through_the_dictionary(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A drop without «Наименование ДО» is loaded by the asset NAME.

    The file spells the entity `АО "БСЗ"` (straight quotes) while the dictionary
    holds «АО «БСЗ»» (guillemets): the name key must match them, otherwise a
    perfectly good file would be refused as an unknown asset.
    """
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_ASSET_BY_NAME",
            asset_key='  ао "бсз" ',
            values={"workshop_name": "Работа по наименованию"},
            asset_is_name=True,
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (1, 0)

    loaded = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_ASSET_BY_NAME")
        )
    ).one()
    assert loaded.asset_id == bsz.id


async def test_an_unknown_asset_name_refuses_the_whole_file(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the dictionary lacks is an error, exactly like an unknown code."""
    rows = [
        ParsedPlanRow(
            code="TEST_ASSET_UNKNOWN_NAME",
            asset_key="АО «Нет такого завода»",
            values={"workshop_name": "неизвестный актив"},
            asset_is_name=True,
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    with pytest.raises(ValueError, match="TEST_ASSET_UNKNOWN_NAME"):
        await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]


async def test_a_clean_file_creates_rows_once_and_updates_them_next_time(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Codes new to the plan append rows; the same file again updates in place.

    The parse step is stubbed so the test owns the labels (a real sheet would
    need the very catalog values the strict load rejects). Everything after the
    parse ? reference resolution, batching, recompute ? runs for real.
    """
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
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
            asset_key="BSZR",
            values={
                "equipment_criticality_group_id": "3",
                "work_category_id": "ТОиР",
                "work_type_id": "КР",
                "cost_allocation_type_id": "OPEX",
                "execution_method_id": "Собственные силы",
                "workshop_name": f"Загруженная работа {index}",
            },
        )
        for index in range(1, 4)
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    first = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (first.new_rows, first.updated_rows) == (3, 0)
    after_first = len(
        (await db_session.exec(select(RenovationPlanRow.id).where(col(RenovationPlanRow.plan_id) == plan.id))).all()
    )
    assert after_first == before + 3

    loaded = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_IMPORT_2")
        )
    ).one()
    assert str(loaded.equipment_criticality_group_id) == _resolve_reference(
        "equipment_criticality_group_id", "3", await _reference_ids(db_session)
    )
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
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(code=f"TEST_PROGRESS_{index}", asset_key="BSZR", values={"workshop_name": f"Работа {index}"})
        for index in range(1, 4)
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)
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
    Blank cells are explicit NULLs, so a real batch is no longer ragged.

    A multi-row INSERT renders ONE `VALUES` clause, which demands identical
    keys per row. The write still pads every row to the batch's key union, so a
    cell the FILE HEADER omitted becomes NULL.

    Dates are the parser's ISO text (its contract — the same value feeds the
    renovation-ID rule) while the columns are DATE; asyncpg rejects a string
    with «'str' object has no attribute 'toordinal'», so the write converts.
    """
    if (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first() is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_RAGGED_1",
            asset_key="BSZR",
            values={
                "equipment_criticality_group_id": "3",
                "work_category_id": "ТОиР",
                "work_type_id": "КР",
                "cost_allocation_type_id": "OPEX",
                "execution_method_id": "Собственные силы",
                "workshop_name": "Работа с датой",
                "work_start_date": "2027-10-01",
            },
        ),
        ParsedPlanRow(
            code="TEST_RAGGED_2",
            asset_key="BSZR",
            values={
                "equipment_criticality_group_id": "3",
                "work_category_id": "ТОиР",
                "work_type_id": "КР",
                "cost_allocation_type_id": "OPEX",
                "execution_method_id": "Собственные силы",
                "workshop_name": "Работа без даты",
                # work_start_date deliberately absent from this row's values:
                # the batch then has ragged key sets and needs key-union padding.
            },
        ),
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)

    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (2, 0)

    loaded = {
        row.work_or_repair_code: row
        for row in (
            await db_session.exec(
                select(RenovationPlanRow).where(
                    col(RenovationPlanRow.work_or_repair_code).in_(["TEST_RAGGED_1", "TEST_RAGGED_2"])
                )
            )
        ).all()
    }
    assert set(loaded) == {"TEST_RAGGED_1", "TEST_RAGGED_2"}
    assert loaded["TEST_RAGGED_1"].work_start_date == date(2027, 10, 1)
    # The omitted cell is NULL, not a neighbour's value.
    assert loaded["TEST_RAGGED_2"].work_start_date is None
    assert loaded["TEST_RAGGED_1"].workshop_name == "Работа с датой"
    assert loaded["TEST_RAGGED_2"].workshop_name == "Работа без даты"


async def test_a_blank_cell_clears_an_existing_value_on_update(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner-approved semantics: an empty source cell overwrites the old value."""
    if (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first() is None:
        pytest.skip("the seed carries no BSZR asset")
    rows = [
        ParsedPlanRow(
            code="TEST_BLANK_UPDATE",
            asset_key="BSZR",
            values={"workshop_name": "Старое значение", "work_start_date": "2027-10-01"},
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)
    await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]

    rows = [
        ParsedPlanRow(
            code="TEST_BLANK_UPDATE",
            asset_key="BSZR",
            values={"workshop_name": None, "work_start_date": None},
        )
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)
    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (0, 1)

    loaded = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_BLANK_UPDATE")
        )
    ).one()
    assert loaded.workshop_name is None
    assert loaded.work_start_date is None


async def test_an_ambiguous_existing_code_is_refused_without_overwriting(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two plan rows share a code: apply refuses instead of a blanket overwrite.

    The unique index is dropped inside this test's transaction so the problematic
    legacy data can be represented; the outer fixture rolls the DDL back, so the
    index is back for every other test.
    """
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    await db_session.execute(text("DROP INDEX uq_r_plan_rows_plan_work_or_repair_code"))  # ty: ignore[deprecated]
    last = (
        await db_session.exec(
            select(col(RenovationPlanRow.position))
            .where(col(RenovationPlanRow.plan_id) == plan.id)
            .order_by(col(RenovationPlanRow.position).desc())
            .limit(1)
        )
    ).first() or 0
    for offset, label in enumerate(("Первая", "Вторая"), start=1):
        db_session.add(
            RenovationPlanRow(
                plan_id=plan.id,
                asset_id=bsz.id,
                position=last + offset,
                work_or_repair_code="TEST_DUP_CODE",
                workshop_name=label,
            )
        )
    await db_session.flush()

    rows = [ParsedPlanRow(code="TEST_DUP_CODE", asset_key="BSZR", values={"workshop_name": "Общее новое значение"})]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)
    with pytest.raises(ValueError, match="TEST_DUP_CODE"):
        await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]

    loaded = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_DUP_CODE")
        )
    ).all()
    assert len(loaded) == 2
    assert {row.workshop_name for row in loaded} == {"Первая", "Вторая"}


async def test_a_multi_batch_mixed_load_issues_one_set_based_update(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """220 rows span two batches, but existing rows are updated by ONE statement."""
    bsz = (await db_session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if bsz is None:
        pytest.skip("the seed carries no BSZR asset")
    plan = (
        await db_session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))
    ).first()
    assert plan is not None
    last = (
        await db_session.exec(
            select(col(RenovationPlanRow.position))
            .where(col(RenovationPlanRow.plan_id) == plan.id)
            .order_by(col(RenovationPlanRow.position).desc())
            .limit(1)
        )
    ).first() or 0
    # 110 rows already in the plan; the file has 220 rows (110 updates + 110 inserts).
    for index in range(1, 111):
        db_session.add(
            RenovationPlanRow(
                plan_id=plan.id,
                asset_id=bsz.id,
                position=last + index,
                work_or_repair_code=f"TEST_MIXED_{index}",
                workshop_name="Старое",
            )
        )
    await db_session.flush()
    rows = [
        ParsedPlanRow(code=f"TEST_MIXED_{index}", asset_key="BSZR", values={"workshop_name": f"Новое {index}"})
        for index in range(1, 221)
    ]
    monkeypatch.setattr("src.renovation.import_apply.parse_plan_rows", lambda _source, _suffix=None: rows)
    update_statements: list[str] = []
    real_execute = db_session.execute  # ty: ignore[deprecated]

    async def counting_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if "UPDATE r_plan_rows" in getattr(statement, "text", ""):
            update_statements.append(statement.text)
        return await real_execute(statement, *args, **kwargs)  # ty: ignore[deprecated]

    monkeypatch.setattr(db_session, "execute", counting_execute)
    result = await apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (110, 110)
    assert len(update_statements) == 1
    assert "workshop_name = s.workshop_name" in update_statements[0]

    updated = (
        await db_session.exec(
            select(RenovationPlanRow).where(col(RenovationPlanRow.work_or_repair_code) == "TEST_MIXED_1")
        )
    ).one()
    assert updated.workshop_name == "Новое 1"
