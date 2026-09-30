"""Regression tests for the batched renovation-plan seed import."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState


async def test_batched_import_flushes_invented_options_before_rows(db_session: AsyncSession) -> None:
    """
    An invented («@») option must be inserted before the rows that point at it.

    `RenovationPlanRow` and `RenovationReferenceValue` are linked only by a bare
    `ForeignKey` — without an ORM relationship SQLAlchemy's unit of work may emit
    the rows first, which Postgres rejects with
    `renovation_plan_rows_col_018_fkey` (the seed-renovation failure).
    """
    from src.scripts.seed_database import insert_renovation_plan_rows

    working = (await db_session.exec(select(RenovationPlan).where(RenovationPlan.state == RPlanState.WORKING))).one()
    max_position = (await db_session.exec(select(func.max(RenovationPlanRow.position)))).one() or 0
    now = datetime.now(UTC)
    label = f"@ПРОБА-{uuid4().hex[:8]}"

    def rows() -> Iterator[RenovationPlanRow]:
        # Same shape as `_ReferenceResolver.resolve`: invent the option first,
        # then yield the row that references it, all in one batch.
        option = RenovationReferenceValue(catalog="work_type", value_key=f"probe_{uuid4().hex[:8]}", label=label)
        db_session.add(option)
        for offset in (1, 2):
            yield RenovationPlanRow(
                plan_id=working.id,
                position=int(max_position) + offset,
                baseline_generation=1,
                revision=0,
                created_at=now,
                updated_at=now,
                updated_by="seed",
                col_018=option.id,
            )

    inserted = await insert_renovation_plan_rows(db_session, working.id, rows(), batch_size=1)

    assert inserted == 2
    stored_option = (
        await db_session.exec(select(RenovationReferenceValue).where(RenovationReferenceValue.label == label))
    ).one_or_none()
    assert stored_option is not None
    stored_rows = (
        await db_session.exec(select(RenovationPlanRow).where(RenovationPlanRow.position > int(max_position)))
    ).all()
    assert {row.col_018 for row in stored_rows} == {stored_option.id}
