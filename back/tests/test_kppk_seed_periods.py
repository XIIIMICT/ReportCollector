"""The КППК seed owns its period rows: it rebuilds them on every run."""

from typing import TYPE_CHECKING

from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import col, select

from src.kppk.models import KppkCost, KppkProduct, KppkRealization, KppkRealizationPlan
from src.scripts import seed_kppk

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession


class _Stub:
    """Stand-in for a seed step: returns a fixed value, swallows any arguments."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        return self.result


async def test_seed_generates_the_periods_before_the_costs(db_session: AsyncSession, monkeypatch) -> None:
    """
    `seed` rebuilds the period rows, and does it BEFORE the cost rebuild.

    The costs are derived from the realization rows, so running them first would
    leave «Себестоимость» computed from the rows the period step is about to
    replace. The heavy copy steps are stubbed out: this pins the WIRING and the
    ORDER, while the rows themselves are covered by `test_kppk_periods`.
    """
    order: list[str] = []
    generated: list[object] = []
    real_generate = seed_kppk._generate_periods

    async def tracking_generate(connection: AsyncConnection, schema: str) -> dict[str, int]:
        order.append("periods")
        generated.append(connection)
        return await real_generate(connection, schema)

    async def tracking_costs(*_args: object, **_kwargs: object) -> int:
        order.append("costs")
        return 0

    # The seeder resolves these by module attribute, so that is the seam.
    monkeypatch.setattr(seed_kppk, "_generate_periods", tracking_generate)
    monkeypatch.setattr(seed_kppk, "_rebuild_costs", tracking_costs)
    monkeypatch.setattr(seed_kppk, "_ensure_copy_tables", _Stub(0))
    monkeypatch.setattr(seed_kppk, "_ensure_schemas", _Stub([]))
    monkeypatch.setattr(seed_kppk, "_ensure_source_tables", _Stub(0))
    monkeypatch.setattr(seed_kppk, "_truncate_copies", _Stub(None))
    monkeypatch.setattr(seed_kppk, "_copy_sources", _Stub({}))
    monkeypatch.setattr(seed_kppk, "_rebuild_org_products", _Stub({"matrix_products_created": 0, "matrix_links": 0}))
    monkeypatch.setattr(seed_kppk, "seed_administrators", _Stub(0))

    counters = await seed_kppk.seed(db_session)

    assert order == ["periods", "costs"], "the periods must be rebuilt before the costs"
    assert len(generated) == 1
    assert "periods_cost" in counters
    # The periods were really written (the fixture's matrix is non-empty).
    rows = (await db_session.exec(select(func.count()).select_from(KppkRealizationPlan))).one()
    assert rows > 0


async def test_every_period_row_references_a_dictionary_row(db_session: AsyncSession) -> None:
    """
    The period rows point at the dictionaries by uuid, never by name.

    The text columns are gone: a product or market is a reference, so an
    orphaned row is impossible by construction — this pins that the FK columns
    are filled for every row the generation writes. The rows have to be
    generated here: the fixture only seeds the dictionaries and the matrix, the
    period rows belong to the seed.
    """
    connection = await db_session.connection()
    schema = (await connection.execute(text("SELECT current_schema()"))).scalar_one()
    counts = await seed_kppk._generate_periods(connection, schema)
    assert counts["kppk_realization_plan"] > 0
    assert counts["kppk_realization"] > 0
    assert counts["kppk_cost"] > 0

    plan_rows = (await db_session.exec(select(KppkRealizationPlan))).all()
    fact_rows = (await db_session.exec(select(KppkRealization))).all()
    cost_rows = (await db_session.exec(select(KppkCost))).all()
    for rows in (plan_rows, fact_rows, cost_rows):
        assert rows
        for row in rows:
            assert row.product_id is not None
    for row in (*plan_rows, *fact_rows):
        assert row.market_id is not None
    # And the dictionaries themselves are intact (no name duplicates either).
    names = (await db_session.exec(select(col(KppkProduct.name)))).all()
    assert len(names) == len(set(names))
