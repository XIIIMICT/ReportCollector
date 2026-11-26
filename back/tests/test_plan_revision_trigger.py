"""Statement-level revision trigger contract on `r_plan_rows` (migration 0043).

Every case runs against the real disposable `rc_test*` schema the suite builds
by applying the full Alembic chain — no mocked session or fake trigger.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState

_SET_CODE = text("UPDATE r_plan_rows SET work_or_repair_code = :code WHERE id = :id")
_SET_PROCESS = text("UPDATE r_plan_rows SET process_position = :value WHERE plan_id = :plan")
_MOVE = text("UPDATE r_plan_rows SET plan_id = :target WHERE id = :id")
_MOVE_ALL = text("UPDATE r_plan_rows SET plan_id = :target WHERE plan_id IN (:a, :b)")
_MOVE_AND_CODE = text("UPDATE r_plan_rows SET plan_id = :target, work_or_repair_code = :code WHERE id = :id")


async def _revision(session: AsyncSession, plan_id: UUID) -> int:
    """Read the counter from the DB, never from a stale identity-map instance."""
    value = (await session.exec(select(col(RenovationPlan.revision)).where(col(RenovationPlan.id) == plan_id))).one()
    return int(value)


async def _working_plan(session: AsyncSession) -> RenovationPlan:
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    assert plan is not None
    return plan


async def _archived_plan(session: AsyncSession, *, project_id: UUID, generation: int) -> RenovationPlan:
    now = datetime.now(UTC)
    plan = RenovationPlan(
        project_id=project_id,
        state=RPlanState.ARCHIVED,
        baseline_generation=generation,
        created_at=now,
        created_by="test",
        updated_at=now,
    )
    session.add(plan)
    await session.flush()
    return plan


async def _asset_id(session: AsyncSession) -> UUID:
    asset = (await session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    assert asset is not None
    return asset.id


async def _insert_row(
    session: AsyncSession, *, plan_id: UUID, asset_id: UUID, position: int, code: str
) -> RenovationPlanRow:
    now = datetime.now(UTC)
    row = RenovationPlanRow(
        plan_id=plan_id,
        asset_id=asset_id,
        position=position,
        baseline_generation=1,
        revision=0,
        created_at=now,
        updated_at=now,
        work_or_repair_code=code,
    )
    session.add(row)
    await session.flush()
    return row


async def _row_plan(session: AsyncSession, row_id: UUID) -> UUID:
    value = (
        await session.exec(select(col(RenovationPlanRow.plan_id)).where(col(RenovationPlanRow.id) == row_id))
    ).one()
    return UUID(str(value))


async def test_code_update_in_one_plan_bumps_its_plan(db_session: AsyncSession) -> None:
    working = await _working_plan(db_session)
    other = await _archived_plan(db_session, project_id=working.project_id, generation=working.baseline_generation + 1)
    row = await _insert_row(
        db_session,
        plan_id=working.id,
        asset_id=await _asset_id(db_session),
        position=100,
        code="TRIGGER_CODE_BEFORE",
    )
    before_working = await _revision(db_session, working.id)
    before_other = await _revision(db_session, other.id)

    await db_session.execute(_SET_CODE, {"id": row.id, "code": "TRIGGER_CODE_AFTER"})  # ty: ignore[deprecated]
    await db_session.flush()

    assert await _revision(db_session, working.id) == before_working + 1
    assert await _revision(db_session, other.id) == before_other


async def test_row_insert_bumps_only_the_target_plan(db_session: AsyncSession) -> None:
    working = await _working_plan(db_session)
    target = await _archived_plan(db_session, project_id=working.project_id, generation=working.baseline_generation + 1)
    before_working = await _revision(db_session, working.id)
    before_target = await _revision(db_session, target.id)

    await _insert_row(
        db_session,
        plan_id=target.id,
        asset_id=await _asset_id(db_session),
        position=101,
        code="TRIGGER_INSERT",
    )
    await db_session.flush()

    assert await _revision(db_session, target.id) == before_target + 1
    assert await _revision(db_session, working.id) == before_working


async def test_row_delete_bumps_only_the_target_plan(db_session: AsyncSession) -> None:
    working = await _working_plan(db_session)
    target = await _archived_plan(db_session, project_id=working.project_id, generation=working.baseline_generation + 1)
    row = await _insert_row(
        db_session,
        plan_id=target.id,
        asset_id=await _asset_id(db_session),
        position=102,
        code="TRIGGER_DELETE",
    )
    before_working = await _revision(db_session, working.id)
    before_target = await _revision(db_session, target.id)

    await db_session.delete(row)
    await db_session.flush()

    assert await _revision(db_session, target.id) == before_target + 1
    assert await _revision(db_session, working.id) == before_working


async def test_move_with_unchanged_code_bumps_both_plans(db_session: AsyncSession) -> None:
    """The RC-1 regression: a row transfer changes BOTH code sets."""
    source = await _working_plan(db_session)
    target = await _archived_plan(db_session, project_id=source.project_id, generation=source.baseline_generation + 1)
    row = await _insert_row(
        db_session,
        plan_id=source.id,
        asset_id=await _asset_id(db_session),
        position=103,
        code="TRIGGER_TRANSFER_UNCHANGED",
    )
    before_source = await _revision(db_session, source.id)
    before_target = await _revision(db_session, target.id)

    await db_session.execute(_MOVE, {"id": row.id, "target": target.id})  # ty: ignore[deprecated]
    await db_session.flush()

    assert await _row_plan(db_session, row.id) == target.id
    assert await _revision(db_session, source.id) == before_source + 1
    assert await _revision(db_session, target.id) == before_target + 1


async def test_move_with_code_change_bumps_both_plans(db_session: AsyncSession) -> None:
    source = await _working_plan(db_session)
    target = await _archived_plan(db_session, project_id=source.project_id, generation=source.baseline_generation + 1)
    row = await _insert_row(
        db_session,
        plan_id=source.id,
        asset_id=await _asset_id(db_session),
        position=104,
        code="TRIGGER_TRANSFER_CODE_OLD",
    )
    before_source = await _revision(db_session, source.id)
    before_target = await _revision(db_session, target.id)

    await db_session.execute(  # ty: ignore[deprecated]
        _MOVE_AND_CODE,
        {"id": row.id, "target": target.id, "code": "TRIGGER_TRANSFER_CODE_NEW"},
    )
    await db_session.flush()

    assert await _row_plan(db_session, row.id) == target.id
    assert await _revision(db_session, source.id) == before_source + 1
    assert await _revision(db_session, target.id) == before_target + 1


async def test_multirow_move_bumps_each_plan_once_per_statement(db_session: AsyncSession) -> None:
    source_a = await _working_plan(db_session)
    generation = source_a.baseline_generation
    source_b = await _archived_plan(db_session, project_id=source_a.project_id, generation=generation + 1)
    target = await _archived_plan(db_session, project_id=source_a.project_id, generation=generation + 2)
    untouched = await _archived_plan(db_session, project_id=source_a.project_id, generation=generation + 3)
    await _insert_row(
        db_session,
        plan_id=source_b.id,
        asset_id=await _asset_id(db_session),
        position=200,
        code="TRIGGER_MULTIROW_B",
    )
    # source_a already carries the fixture's two rows (positions 1 and 2).
    before = {plan.id: await _revision(db_session, plan.id) for plan in (source_a, source_b, target, untouched)}

    # One statement: two rows leave A, one leaves B, three arrive in C. Each of
    # A/B/C must be bumped exactly once, never once per transferred row.
    await db_session.execute(  # ty: ignore[deprecated]
        _MOVE_ALL,
        {"target": target.id, "a": source_a.id, "b": source_b.id},
    )
    await db_session.flush()

    for plan in (source_a, source_b, target):
        assert await _revision(db_session, plan.id) == before[plan.id] + 1
    assert await _revision(db_session, untouched.id) == before[untouched.id]


async def test_unrelated_update_and_no_op_code_write_do_not_bump(db_session: AsyncSession) -> None:
    plan = await _working_plan(db_session)
    row = await _insert_row(
        db_session,
        plan_id=plan.id,
        asset_id=await _asset_id(db_session),
        position=105,
        code="TRIGGER_UNCHANGED_CODE",
    )
    before = await _revision(db_session, plan.id)

    await db_session.execute(_SET_PROCESS, {"value": "Шкаф ПЛК", "plan": plan.id})  # ty: ignore[deprecated]
    await db_session.flush()
    assert await _revision(db_session, plan.id) == before

    # A recompute that rewrites the same code must not invalidate the cache.
    await db_session.execute(  # ty: ignore[deprecated]
        _SET_CODE,
        {"id": row.id, "code": "TRIGGER_UNCHANGED_CODE"},
    )
    await db_session.flush()
    assert await _revision(db_session, plan.id) == before


@pytest.mark.real_schema
async def test_rolled_back_move_does_not_commit_its_bump(db_session: AsyncSession, my_engine: AsyncEngine) -> None:
    """The revision bump lives in the same transaction as the row write."""
    source = await _working_plan(db_session)
    target = await _archived_plan(db_session, project_id=source.project_id, generation=source.baseline_generation + 1)
    row = await _insert_row(
        db_session,
        plan_id=source.id,
        asset_id=await _asset_id(db_session),
        position=106,
        code="TRIGGER_ROLLBACK",
    )
    source_id, target_id, row_id = source.id, target.id, row.id
    await db_session.commit()
    before_source = await _revision(db_session, source_id)
    before_target = await _revision(db_session, target_id)

    await db_session.execute(_MOVE, {"id": row_id, "target": target_id})  # ty: ignore[deprecated]
    await db_session.flush()
    assert await _revision(db_session, source_id) == before_source + 1
    assert await _revision(db_session, target_id) == before_target + 1
    await db_session.rollback()

    # A new connection must see neither the transfer nor its revision bumps.
    factory = async_sessionmaker(my_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as reader:
        assert await _revision(reader, source_id) == before_source
        assert await _revision(reader, target_id) == before_target
        assert await _row_plan(reader, row_id) == source_id
