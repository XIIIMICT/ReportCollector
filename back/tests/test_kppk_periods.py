"""Period rows of the КППК seed: the whole window, rebuilt from scratch."""

from datetime import date

from sqlalchemy import func, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.kppk.models import KppkCost, KppkMarket, KppkOrgProduct, KppkRealization, KppkRealizationPlan
from src.scripts import seed_kppk
from src.scripts.seed_kppk import target_periods


def test_target_periods_starts_in_september_2023_and_reaches_next_december() -> None:
    """September 2023 through December of the year after `today`, calendar-aligned."""
    periods = target_periods(date(2026, 10, 20))
    assert periods[0] == date(2023, 9, 1)
    assert periods[-1] == date(2027, 12, 1)
    # Every month of every year in between — no gaps, calendar-aligned.
    assert all(period.day == 1 for period in periods)
    assert len(periods) == 4 + 12 * 3 + 12  # Sep..Dec 2023 + 2024..2026 + 2027


def test_target_periods_shifts_with_the_current_year() -> None:
    """The end of the window is relative: next December, whatever "today" is."""
    assert target_periods(date(2025, 3, 1))[-1] == date(2026, 12, 1)
    assert target_periods(date(2030, 1, 1))[-1] == date(2031, 12, 1)
    assert target_periods(date(2030, 1, 1))[0] == date(2023, 9, 1)


async def test_seed_rebuilds_the_periods_and_clears_stale_rows(db_session: AsyncSession) -> None:
    """
    The rebuild regenerates the EMPTY scaffolding and keeps what carries data.

    A stale row (a product removed from the matrix, a period outside the window)
    must not survive — but a row that holds figures is real data (the source's or
    a user's) and is never thrown away. The fixture already seeds three rows
    carrying figures at 2026-06.
    """
    pairs = (await db_session.exec(select(KppkOrgProduct))).all()
    assert pairs, "the КППК fixture must link products to organizations"

    # Rows that carry figures BEFORE the rebuild — they must survive it.
    kept_realization = int(
        (
            await db_session.exec(
                select(func.count()).select_from(KppkRealization).where(col(KppkRealization.fact).is_not(None))
            )
        ).one()
    )
    kept_plan = int(
        (
            await db_session.exec(
                select(func.count())
                .select_from(KppkRealizationPlan)
                .where(col(KppkRealizationPlan.month_bp_plan).is_not(None))
            )
        ).one()
    )
    assert kept_realization > 0, "the fixture is expected to seed data-carrying rows"

    # A row outside the window: the rebuild must remove it.
    market_id = (await db_session.exec(select(col(KppkMarket.id)))).first()
    assert market_id is not None
    db_session.add(
        KppkRealization(
            org_id=pairs[0].org_id,
            period_date=date(2020, 1, 1),
            rep_date=date(2020, 1, 31),
            product_id=pairs[0].product_id,
            market_id=market_id,
        )
    )
    await db_session.commit()

    connection = await db_session.connection()
    schema = (await connection.execute(text("SELECT current_schema()"))).scalar_one()
    counts = await seed_kppk._generate_periods(connection, schema)

    periods = target_periods()
    markets = (await db_session.exec(select(func.count()).select_from(KppkMarket))).one()
    after_plan = (
        await db_session.exec(
            select(func.count())
            .select_from(KppkRealizationPlan)
            .where(col(KppkRealizationPlan.month_bp_plan).is_not(None))
        )
    ).one()
    # Generated scaffolding + the data-carrying rows the rebuild preserved.
    assert after_plan == kept_plan, f"data-carrying plan rows: {kept_plan} -> {after_plan}"
    # One row per key: the scaffolding never sits beside a preserved row.
    rows = (await db_session.exec(select(KppkRealizationPlan))).all()
    keys = {(row.org_id, row.period_date, row.product_id, row.market_id) for row in rows}
    assert len(keys) == len(rows), "one row per key"
    # Every generated pair is present, and the preserved rows are on top of it
    # (a preserved row may occupy a key the scaffolding would also generate).
    assert len(rows) >= len(pairs) * len(periods) * markets
    assert counts["kppk_realization_plan"] == len(rows)
    assert counts["kppk_cost"] == len(pairs) * len(periods)

    # The stale row (a period outside the window) is gone.
    stale = (
        await db_session.exec(
            select(func.count())
            .select_from(KppkRealization)
            .where(col(KppkRealization.period_date) == date(2020, 1, 1))
        )
    ).one()
    assert stale == 0

    # Every market exists in the first and the last month, for every pair.
    for period in (periods[0], periods[-1]):
        for model in (KppkRealizationPlan, KppkRealization):
            rows = (
                await db_session.exec(select(func.count()).select_from(model).where(col(model.period_date) == period))
            ).one()
            assert rows == len(pairs) * markets, f"{model.__name__} in {period}: {rows}"

    # The figures start at zero — the forms exist to be filled.
    total = (await db_session.exec(select(func.coalesce(func.sum(col(KppkCost.full_cost_bp)), 0)))).one()
    assert float(total) == 0.0
