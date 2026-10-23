"""Replacement in the КППК dictionaries: raw items and ДЗО."""

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.kppk.core import _now
from src.kppk.dictionaries import replace_org, replace_raw
from src.kppk.models import KppkOrg, KppkOrgProduct, KppkProduct, KppkRaw


async def test_replacing_a_raw_item_deletes_the_replaced_one(db_session: AsyncSession) -> None:
    raws = (await db_session.exec(select(KppkRaw))).all()
    if len(raws) < 2:
        pytest.skip("the seed carries less than two raw items")
    source, target = raws[0], raws[1]

    applied = await replace_raw(db_session, raw_id=source.id, target_id=target.id)

    assert applied[0].field == "__deleted"
    assert await db_session.get(KppkRaw, source.id) is None
    assert await db_session.get(KppkRaw, target.id) is not None


async def test_replacing_an_org_moves_its_product_positions(db_session: AsyncSession) -> None:
    now = _now()
    # Hand-made ДЗО carry no source number, so the replacement may delete them.
    source = KppkOrg(code="PROBA", name="ПРОБА-ИСТОЧНИК", created_at=now, updated_at=now)
    target = KppkOrg(code="PROBT", name="ПРОБА-ЦЕЛЬ", created_at=now, updated_at=now)
    db_session.add(source)
    db_session.add(target)
    await db_session.flush()
    product = KppkProduct(name="ПРОБА-ЗАМЕНА", created_at=now, updated_at=now)
    extra = KppkProduct(name="ПРОБА-ПЕРЕНОС", created_at=now, updated_at=now)
    db_session.add(product)
    db_session.add(extra)
    await db_session.flush()
    # The target already holds `product`: the moved duplicate must merge away.
    db_session.add(KppkOrgProduct(org_id=target.id, product_id=product.id, created_at=now, updated_at=now))
    db_session.add(KppkOrgProduct(org_id=source.id, product_id=product.id, created_at=now, updated_at=now))
    db_session.add(KppkOrgProduct(org_id=source.id, product_id=extra.id, created_at=now, updated_at=now))
    await db_session.flush()

    applied = await replace_org(db_session, org_id=source.id, target_id=target.id)

    assert applied[0].field == "__replaced"
    assert await db_session.get(KppkOrg, source.id) is None
    moved = (await db_session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == target.id))).all()
    product_ids = [link.product_id for link in moved]
    # The duplicate merged into one position, the other product moved over.
    assert product_ids.count(product.id) == 1
    assert extra.id in product_ids
