"""Rejected cell edits on the «Движение» and «Цены» forms.

The happy paths are covered through the API in `test_raw_materials.py`. What was
not covered (and is what breaks in production) is the guard layer: which edits
the service REFUSES and what it refuses them with. Each case here pins one
branch of `update_movement_cells` / `update_price_cells`, so a guard that
silently stops firing shows up as an edit that succeeds where it must not.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User
from src.raw_materials.models import (
    AssetRawMaterial,
    MaterialMovement,
    RawMaterialAsset,
    RawMaterialPrice,
)
from src.raw_materials.schemas import CellUpdate
from src.raw_materials.service import CellValidationError, update_movement_cells, update_price_cells

MONTH = date(2026, 8, 1)
SESSION = "rejected-edits"


async def _admin_and_scope(
    session: AsyncSession,
) -> tuple[User, Sequence[MaterialMovement], list[RawMaterialAsset]]:
    user = (await session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    rows = (await session.exec(select(MaterialMovement).where(MaterialMovement.report_month == MONTH))).all()
    assets = list((await session.exec(select(RawMaterialAsset))).all())
    return user, rows, assets


async def test_a_cell_update_without_a_row_is_refused_by_the_schema() -> None:
    """Refuse a cell update with no row at the schema boundary.

    The guard inside the service is unreachable through the API: pydantic
    rejects a missing `rowId` first, so that check stays as defence in depth.
    """
    with pytest.raises(ValueError, match="rowId"):
        CellUpdate.model_validate({"rowId": None, "field": "balanceOnSiteTons", "value": 1})


async def test_a_row_from_another_month_is_refused(db_session) -> None:
    """Editing through a month that is not open never touches the other month."""
    user, rows, assets = await _admin_and_scope(db_session)

    with pytest.raises(CellValidationError):
        await update_movement_cells(
            db_session,
            report_month=date(2030, 1, 1),
            updates=[CellUpdate(rowId=rows[0].id, field="balanceOnSiteTons", value=Decimal(1))],
            user=user,
            assets=assets,
            session_id=SESSION,
        )


async def test_an_unknown_movement_field_is_refused(db_session) -> None:
    user, rows, assets = await _admin_and_scope(db_session)

    with pytest.raises(ValueError, match="Неизвестное поле"):
        await update_movement_cells(
            db_session,
            report_month=MONTH,
            updates=[CellUpdate(rowId=rows[0].id, field="noSuchField", value=1)],
            user=user,
            assets=assets,
            session_id=SESSION,
        )


async def test_a_price_field_without_a_price_row_is_refused(db_session) -> None:
    """A movement row whose arm has no «Цены» row cannot take a price edit."""
    user, rows, assets = await _admin_and_scope(db_session)
    arm_ids = {row.asset_raw_material_id for row in rows}
    prices = (
        await db_session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id).in_(arm_ids)))
    ).all()
    priced = {price.asset_raw_material_id for price in prices}
    unpriced = next((row for row in rows if row.asset_raw_material_id not in priced), None)
    if unpriced is None:
        pytest.skip("every seeded movement row already has a price row")

    with pytest.raises(ValueError, match="не найдена цена"):
        await update_movement_cells(
            db_session,
            report_month=MONTH,
            updates=[CellUpdate(rowId=unpriced.id, field="deliveryPriceTons", value=Decimal(1))],
            user=user,
            assets=assets,
            session_id=SESSION,
        )


async def test_editing_a_row_outside_the_granted_assets_is_refused(db_session) -> None:
    """Asset scoping is enforced in the service, not only in the router."""
    user, rows, assets = await _admin_and_scope(db_session)
    arms = {arm.id: arm for arm in (await db_session.exec(select(AssetRawMaterial))).all()}
    allowed_asset = assets[0]
    foreign = next(
        (row for row in rows if (arm := arms.get(row.asset_raw_material_id)) and arm.asset_id != allowed_asset.id),
        None,
    )
    if foreign is None:
        pytest.skip("the seed has no movement row outside the first asset")

    with pytest.raises(ValueError, match="Нет доступа"):
        await update_movement_cells(
            db_session,
            report_month=MONTH,
            updates=[CellUpdate(rowId=foreign.id, field="balanceOnSiteTons", value=Decimal(1))],
            user=user,
            assets=[allowed_asset],
            session_id=SESSION,
        )


async def test_an_unchanged_value_is_not_written_as_an_edit(db_session) -> None:
    """Re-committing the same number must not produce an undo entry."""
    user, rows, assets = await _admin_and_scope(db_session)
    row = rows[0]

    applied = await update_movement_cells(
        db_session,
        report_month=MONTH,
        updates=[CellUpdate(rowId=row.id, field="balanceOnSiteTons", value=row.balance_on_site_tons)],
        user=user,
        assets=assets,
        session_id=SESSION,
    )

    assert applied == []


async def test_a_price_row_from_another_row_scope_is_refused(db_session) -> None:
    user, _, assets = await _admin_and_scope(db_session)
    prices = (await db_session.exec(select(RawMaterialPrice))).all()
    if not prices:
        pytest.skip("no price rows in the seed")

    with pytest.raises(ValueError, match="Строка «Цен» не найдена"):
        await update_price_cells(
            db_session,
            updates=[CellUpdate(rowId=uuid4(), field="deliveryPriceTons", value=Decimal(1))],
            user=user,
            assets=assets,
            session_id=SESSION,
        )


async def test_an_unknown_price_field_is_refused(db_session) -> None:
    user, _, assets = await _admin_and_scope(db_session)
    prices = (await db_session.exec(select(RawMaterialPrice))).all()
    if not prices:
        pytest.skip("no price rows in the seed")

    with pytest.raises(ValueError, match="Неизвестное поле"):
        await update_price_cells(
            db_session,
            updates=[CellUpdate(rowId=prices[0].id, field="noSuchField", value=1)],
            user=user,
            assets=assets,
            session_id=SESSION,
        )


async def test_a_price_row_of_a_foreign_asset_is_refused(db_session) -> None:
    user, _, assets = await _admin_and_scope(db_session)
    prices = (await db_session.exec(select(RawMaterialPrice))).all()
    if not prices:
        pytest.skip("no price rows in the seed")

    with pytest.raises(ValueError, match="Нет доступа"):
        await update_price_cells(
            db_session,
            updates=[CellUpdate(rowId=prices[0].id, field="deliveryPriceTons", value=Decimal(1))],
            user=user,
            assets=[assets[-1]],
            session_id=SESSION,
        )
