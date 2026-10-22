"""The `gen-kppk-periods` command: rows for the coming periods, idempotently."""

from datetime import date

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.kppk.constants import MARKET_CODES
from src.kppk.models import KppkOrgProduct
from src.scripts.generate_kppk_periods import generate, target_periods


def test_target_periods_covers_this_and_next_year() -> None:
    """January of the current year through December of the next one — 24 months."""
    periods = target_periods(date(2026, 10, 20))
    assert len(periods) == 24
    assert periods[0] == date(2026, 1, 1)
    assert periods[-1] == date(2027, 12, 1)
    # Every month, calendar-aligned (the copies reject a mid-month period_date).
    assert all(period.day == 1 for period in periods)


async def test_generate_adds_the_missing_rows_once(db_session: AsyncSession) -> None:
    """
    Add one row per market (and one cost row) per matrix product and period, once.

    A second run adds nothing — the command is idempotent. The seeded fixture
    already holds some rows for 2026, so the generator only tops up what is
    missing; the assertion is on the SHAPE (every matrix pair has all markets in
    the first and the last period), not on the raw count.
    """
    pairs = (await db_session.exec(select(KppkOrgProduct))).all()
    assert pairs, "the КППК fixture must link products to organizations"

    first = await generate(session=db_session)
    assert first["kppk_realization_plan"] > 0
    assert first["kppk_realization"] > 0
    assert first["kppk_cost"] > 0

    second = await generate(session=db_session)
    assert second == {
        "kppk_realization_plan": 0,
        "kppk_realization": 0,
        "kppk_cost": 0,
    }

    from sqlalchemy import func
    from sqlmodel import col

    from src.kppk.models import KppkCost, KppkRealizationPlan

    periods = target_periods()
    for period in (periods[0], periods[-1]):
        for pair in pairs:
            rows = (
                await db_session.exec(
                    select(func.count())
                    .select_from(KppkRealizationPlan)
                    .where(col(KppkRealizationPlan.org_id) == pair.org_id)
                    .where(col(KppkRealizationPlan.period_date) == period)
                )
            ).one()
            # All three markets exist, whatever the source dump held.
            assert rows >= len(MARKET_CODES), f"realization rows for {pair.org_id} in {period}: {rows}"
            costs = (
                await db_session.exec(
                    select(func.count())
                    .select_from(KppkCost)
                    .where(col(KppkCost.org_id) == pair.org_id)
                    .where(col(KppkCost.period_date) == period)
                )
            ).one()
            assert costs, f"no cost rows for {pair.org_id} in {period}"
