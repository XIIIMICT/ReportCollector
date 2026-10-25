"""Replacement in the «КППК» dictionaries: raw items, ДЗО and products."""

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User
from src.kppk.core import KppkValidationError, _now
from src.kppk.dictionaries import replace_org, replace_product, replace_raw
from src.kppk.models import KppkOrg, KppkOrgProduct, KppkProduct, KppkRaw


async def _admin(session: AsyncSession) -> User:
    return (await session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()


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


async def test_replacing_a_product_moves_its_positions(db_session: AsyncSession) -> None:
    now = _now()
    user = await _admin(db_session)
    org_a = KppkOrg(code="PRDA", name="ПРОБА-А", created_at=now, updated_at=now)
    org_b = KppkOrg(code="PRDB", name="ПРОБА-Б", created_at=now, updated_at=now)
    db_session.add(org_a)
    db_session.add(org_b)
    await db_session.flush()
    source = KppkProduct(name="ПРОБА-ИСТОЧНИК", created_at=now, updated_at=now)
    target = KppkProduct(name="ПРОБА-ЦЕЛЬ", created_at=now, updated_at=now)
    db_session.add(source)
    db_session.add(target)
    await db_session.flush()
    # org_a exists on BOTH products: the moved row merges into the one position,
    # and its excise flag survives the merge.
    db_session.add(KppkOrgProduct(org_id=org_a.id, product_id=target.id, created_at=now, updated_at=now))
    db_session.add(
        KppkOrgProduct(org_id=org_a.id, product_id=source.id, has_excise=True, created_at=now, updated_at=now)
    )
    picked = KppkOrgProduct(org_id=org_b.id, product_id=source.id, created_at=now, updated_at=now)
    db_session.add(picked)
    await db_session.flush()

    applied = await replace_product(db_session, position_id=picked.id, target_id=target.id, user=user)

    assert applied[0].field == "__replaced"
    assert await db_session.get(KppkProduct, source.id) is None
    links = (await db_session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == target.id))).all()
    assert {link.org_id for link in links} == {org_a.id, org_b.id}
    assert len(links) == 2
    merged = next(link for link in links if link.org_id == org_a.id)
    assert merged.has_excise is True


async def test_a_numbered_product_cannot_be_replaced(db_session: AsyncSession) -> None:
    now = _now()
    user = await _admin(db_session)
    org = KppkOrg(code="PRDC", name="ПРОБА-В", created_at=now, updated_at=now)
    db_session.add(org)
    await db_session.flush()
    source = KppkProduct(product_num=987654, name="ПРОБА-НОМЕР", created_at=now, updated_at=now)
    target = KppkProduct(name="ПРОБА-ЦЕЛЬ-2", created_at=now, updated_at=now)
    db_session.add(source)
    db_session.add(target)
    await db_session.flush()
    link = KppkOrgProduct(org_id=org.id, product_id=source.id, created_at=now, updated_at=now)
    db_session.add(link)
    await db_session.flush()

    with pytest.raises(KppkValidationError, match="номер"):
        await replace_product(db_session, position_id=link.id, target_id=target.id, user=user)


async def test_replacing_a_product_with_itself_is_refused(db_session: AsyncSession) -> None:
    now = _now()
    user = await _admin(db_session)
    org = KppkOrg(code="PRDD", name="ПРОБА-Г", created_at=now, updated_at=now)
    db_session.add(org)
    await db_session.flush()
    product = KppkProduct(name="ПРОБА-САМ", created_at=now, updated_at=now)
    db_session.add(product)
    await db_session.flush()
    link = KppkOrgProduct(org_id=org.id, product_id=product.id, created_at=now, updated_at=now)
    db_session.add(link)
    await db_session.flush()

    with pytest.raises(KppkValidationError, match="самое себя"):
        await replace_product(db_session, position_id=link.id, target_id=product.id, user=user)


async def test_the_grouped_row_id_is_the_product(db_session: AsyncSession) -> None:
    """The grouped view edits a PRODUCT row: its id is the product, not a position."""
    now = _now()
    user = await _admin(db_session)
    org = KppkOrg(code="PRDE", name="ПРОБА-Д", created_at=now, updated_at=now)
    db_session.add(org)
    await db_session.flush()
    source = KppkProduct(name="ПРОБА-ГРУППА", created_at=now, updated_at=now)
    target = KppkProduct(name="ПРОБА-ГРУППА-ЦЕЛЬ", created_at=now, updated_at=now)
    db_session.add(source)
    db_session.add(target)
    await db_session.flush()
    db_session.add(KppkOrgProduct(org_id=org.id, product_id=source.id, created_at=now, updated_at=now))
    await db_session.flush()

    # The PRODUCT id, exactly what the grouped grid sends.
    applied = await replace_product(db_session, position_id=source.id, target_id=target.id, user=user)

    assert applied[0].field == "__replaced"
    assert await db_session.get(KppkProduct, source.id) is None
    links = (await db_session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == target.id))).all()
    assert [link.org_id for link in links] == [org.id]


async def test_a_position_less_product_is_removed_by_its_own_id(db_session: AsyncSession) -> None:
    now = _now()
    user = await _admin(db_session)
    product = KppkProduct(name="ПРОБА-БЕЗ-ДЗО", created_at=now, updated_at=now)
    db_session.add(product)
    await db_session.flush()

    applied = await replace_product(db_session, position_id=product.id, target_id=None, user=user)

    assert applied[0].field == "__deleted"
    assert await db_session.get(KppkProduct, product.id) is None
