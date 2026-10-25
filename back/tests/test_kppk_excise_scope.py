"""The excise scope: only flagged ДЗО × product pairs may carry one."""

from conftest import signed_identity_headers
from sqlalchemy import update
from sqlmodel import col, select

from src.kppk.models import KppkOrg, KppkOrgProduct, KppkProduct, KppkRealizationPlan

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")


async def test_toggling_the_flag_keeps_the_entered_excise(client, db_session) -> None:
    """
    Keep the entered rates when the excise switch is toggled.

    «Заполняемый акциз» is only a switch: turning it off and on again must not
    clear the rates somebody already typed.

    The figures live on the realization rows (`excise_bp` / `excise_max_plan` /
    `excise_fact`), the flag lives on the position — the toggle touches only the
    second. This pins that: a regression that "resets" the numbers when the
    column is switched off would silently destroy entered data.
    """
    # Take a realization row and resolve the position that owns the flag — the
    # fixture's matrix does not necessarily cover the copy's product, so the
    # pair is created when missing (the flag is what this test is about).
    row = (await db_session.exec(select(KppkRealizationPlan))).first()
    assert row is not None
    product = (await db_session.exec(select(KppkProduct).where(col(KppkProduct.id) == row.product_id))).first()
    assert product is not None
    position = (
        await db_session.exec(
            select(KppkOrgProduct).where(
                col(KppkOrgProduct.org_id) == row.org_id,
                col(KppkOrgProduct.product_id) == product.id,
            )
        )
    ).first()
    if position is None:
        position = KppkOrgProduct(org_id=row.org_id, product_id=product.id)
        db_session.add(position)
        await db_session.commit()
    assert position is not None

    # Flag the position on, enter a rate, then switch the column off and on.
    await db_session.execute(
        update(KppkOrgProduct).where(col(KppkOrgProduct.id) == position.id).values(has_excise=True)
    )
    await db_session.commit()
    response = await client.patch(
        f"/api/kppk/realization/{row.id}",
        headers=ADMIN_HEADERS,
        params={"slice": "bp", "session_id": "excise-keep"},
        json={"field": "exciseBp", "value": 42},
    )
    assert response.status_code == 200, response.text

    for flag in (False, True):
        response = await client.patch(
            f"/api/kppk/product-positions/{position.id}",
            headers=ADMIN_HEADERS,
            params={"session_id": "excise-keep"},
            json={"field": "hasExcise", "value": flag},
        )
        assert response.status_code == 200, response.text
        assert response.json()["row"]["hasExcise"] is flag

    kept = (
        await db_session.exec(
            select(KppkRealizationPlan)
            .where(col(KppkRealizationPlan.id) == row.id)
            .execution_options(populate_existing=True)
        )
    ).one()
    assert float(kept.excise_bp) == 42.0


async def test_an_asset_without_the_flag_has_no_excise_products(client, db_session) -> None:
    """The per-position switches gate the whole column: none flagged → nothing."""
    org = (await db_session.exec(select(KppkOrg))).first()
    assert org is not None
    await db_session.execute(
        update(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == org.id).values(has_excise=False)
    )
    await db_session.commit()
    from src.kppk.realization import read_excise_product_ids

    assert await read_excise_product_ids(db_session, org_id=org.id) == set()


async def test_an_excise_outside_the_scope_is_rejected(client, db_session) -> None:
    """
    The server refuses an excise on a product × ДЗО pair that is not flagged.

    The grid locks the cell, but a stale page or a direct call must not sneak a
    rate in: the rule is the same one the column's visibility follows.
    """
    # The fixture seeds realization rows for one ДЗО only: take a row and use
    # ITS asset (the first org may well be a different one).
    row = (await db_session.exec(select(KppkRealizationPlan))).first()
    assert row is not None
    org = await db_session.get(KppkOrg, row.org_id)
    assert org is not None
    # No position of the asset is flagged → no excise there.
    await db_session.commit()
    response = await client.patch(
        f"/api/kppk/realization/{row.id}",
        headers=ADMIN_HEADERS,
        params={"slice": "bp", "session_id": "excise-scope"},
        json={"field": "exciseBp", "value": 15},
    )
    assert response.status_code == 400
    assert "акциз не предусмотрен" in response.json()["detail"]


async def test_the_flag_lists_only_its_own_products(client, db_session) -> None:
    """Only the flagged product × ДЗО positions come back for the asset."""
    from src.kppk.realization import read_excise_product_ids

    org = (await db_session.exec(select(KppkOrg))).first()
    assert org is not None
    pairs = (await db_session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == org.id))).all()
    assert pairs, "the fixture must link a product to the asset"
    flagged = pairs[0]
    await db_session.execute(update(KppkOrgProduct).where(col(KppkOrgProduct.id) == flagged.id).values(has_excise=True))
    await db_session.execute(
        update(KppkOrgProduct)
        .where(col(KppkOrgProduct.id) != flagged.id, col(KppkOrgProduct.org_id) == org.id)
        .values(has_excise=False)
    )
    await db_session.commit()

    product = (await db_session.exec(select(KppkProduct).where(col(KppkProduct.id) == flagged.product_id))).one()
    assert await read_excise_product_ids(db_session, org_id=org.id) == {product.id}
