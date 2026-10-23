"""The КППК seed finishes by materializing the coming periods."""

from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import col, select

from src.kppk.models import KppkCost, KppkRealization, KppkRealizationPlan
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


async def test_seed_generates_the_periods_as_its_last_step(db_session: AsyncSession, monkeypatch) -> None:
    """
    Run the period generator from `seed`, on the seed's own session.

    A fresh copy otherwise leaves the «Реализация»/«Себестоимость» forms empty
    for every month nobody loaded. The heavy copy steps are stubbed out: this
    pins the WIRING and that the counters reach the summary, while `generate`
    itself is covered by `test_kppk_periods`.
    """
    real_generate = seed_kppk.generate
    sessions: list[object] = []

    async def counting_generate(*, session: AsyncSession, dry_run: bool = False) -> dict[str, int]:
        sessions.append(session)
        return await real_generate(session=session, dry_run=dry_run)

    # The seeder resolves these by module attribute, so that is the seam.
    monkeypatch.setattr(seed_kppk, "generate", counting_generate)
    monkeypatch.setattr(seed_kppk, "_ensure_copy_tables", _Stub(0))
    monkeypatch.setattr(seed_kppk, "_ensure_schemas", _Stub([]))
    monkeypatch.setattr(seed_kppk, "_ensure_source_tables", _Stub(0))
    monkeypatch.setattr(seed_kppk, "_truncate_copies", _Stub(None))
    monkeypatch.setattr(seed_kppk, "_copy_sources", _Stub({}))
    monkeypatch.setattr(seed_kppk, "_rebuild_org_products", _Stub({"matrix_products_created": 0, "matrix_links": 0}))
    monkeypatch.setattr(seed_kppk, "_rebuild_costs", _Stub(0))
    monkeypatch.setattr(seed_kppk, "seed_administrators", _Stub(0))

    counters = await seed_kppk.seed(db_session)

    assert sessions == [db_session], "the seed must run the generator once, on its own session"
    assert "periods_cost" in counters
    # The periods were really written (the fixture's matrix is non-empty).
    rows = (await db_session.exec(select(func.count()).select_from(KppkRealizationPlan))).one()
    assert rows > 0


async def test_seeded_rows_have_no_stray_whitespace(db_session: AsyncSession) -> None:
    """The copies keep names trimmed and single-spaced: the seed normalizes them."""
    plan_products = (await db_session.exec(select(col(KppkRealizationPlan.product)))).all()
    fact_products = (await db_session.exec(select(col(KppkRealization.product)))).all()
    cost_products = (await db_session.exec(select(col(KppkCost.product)))).all()
    for value in (*plan_products, *fact_products, *cost_products):
        assert value == " ".join(value.split()), repr(value)
