"""Asset replacement merges the asset × raw-material links without duplicates."""

from datetime import UTC, date, datetime

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.raw_materials.dictionaries import replace_asset
from src.raw_materials.models import (
    AssetRawMaterial,
    MaterialMovement,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
)


def _now() -> datetime:
    return datetime.now(UTC)


async def test_replacing_an_asset_merges_the_product_links(db_session: AsyncSession) -> None:
    now = _now()
    source = RawMaterialAsset(name="ПРОБА-АКТИВ-1", created_at=now, updated_at=now)
    target = RawMaterialAsset(name="ПРОБА-АКТИВ-2", created_at=now, updated_at=now)
    shared = RawMaterial(name="ПРОБА-СЫРЬЁ-ОБЩЕЕ", created_at=now, updated_at=now)
    only_source = RawMaterial(name="ПРОБА-СЫРЬЁ-ОДНО", created_at=now, updated_at=now)
    db_session.add(source)
    db_session.add(target)
    db_session.add(shared)
    db_session.add(only_source)
    await db_session.flush()

    # Same raw material on both assets: the links must merge into one.
    source_shared = AssetRawMaterial(asset_id=source.id, raw_material_id=shared.id, created_at=now, updated_at=now)
    target_shared = AssetRawMaterial(asset_id=target.id, raw_material_id=shared.id, created_at=now, updated_at=now)
    source_only = AssetRawMaterial(asset_id=source.id, raw_material_id=only_source.id, created_at=now, updated_at=now)
    db_session.add(source_shared)
    db_session.add(target_shared)
    db_session.add(source_only)
    await db_session.flush()

    # A month the target already covers (dropped) and one it does not (moved).
    march, august = date(2026, 8, 1), date(2026, 9, 1)
    db_session.add(
        MaterialMovement(asset_raw_material_id=target_shared.id, report_month=march, created_at=now, updated_at=now)
    )
    db_session.add(
        MaterialMovement(asset_raw_material_id=source_shared.id, report_month=march, created_at=now, updated_at=now)
    )
    db_session.add(
        MaterialMovement(asset_raw_material_id=source_shared.id, report_month=august, created_at=now, updated_at=now)
    )
    db_session.add(RawMaterialPrice(asset_raw_material_id=source_shared.id, created_at=now, updated_at=now))
    await db_session.flush()

    await replace_asset(db_session, asset_id=source.id, target_id=target.id)

    links = (await db_session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.asset_id) == target.id))).all()
    assert sorted(link.raw_material_id for link in links) == sorted([shared.id, only_source.id])
    merged = next(link for link in links if link.raw_material_id == shared.id)
    months = sorted(
        row.report_month
        for row in (
            await db_session.exec(
                select(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id) == merged.id)
            )
        ).all()
    )
    assert months == [march, august]
    prices = (
        await db_session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id) == merged.id))
    ).all()
    assert len(prices) == 1
    assert await db_session.get(RawMaterialAsset, source.id) is None
