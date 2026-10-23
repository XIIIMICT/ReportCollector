"""Asset replacement re-points the asset × raw-material links or reports a conflict."""

from datetime import UTC, datetime

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.raw_materials.dictionaries import replace_asset
from src.raw_materials.models import AssetRawMaterial, RawMaterial, RawMaterialAsset


def _now() -> datetime:
    return datetime.now(UTC)


async def _assets(source: AsyncSession) -> tuple[RawMaterialAsset, RawMaterialAsset]:
    now = _now()
    first = RawMaterialAsset(name="ПРОБА-АКТИВ-1", created_at=now, updated_at=now)
    second = RawMaterialAsset(name="ПРОБА-АКТИВ-2", created_at=now, updated_at=now)
    source.add(first)
    source.add(second)
    await source.flush()
    return first, second


async def test_a_unique_link_follows_the_asset(db_session: AsyncSession) -> None:
    source, target = await _assets(db_session)
    now = _now()
    material = RawMaterial(name="ПРОБА-СЫРЬЁ-ОДНО", created_at=now, updated_at=now)
    db_session.add(material)
    await db_session.flush()
    db_session.add(AssetRawMaterial(asset_id=source.id, raw_material_id=material.id, created_at=now, updated_at=now))
    await db_session.flush()

    await replace_asset(db_session, asset_id=source.id, target_id=target.id)

    links = (await db_session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.asset_id) == target.id))).all()
    assert [link.raw_material_id for link in links] == [material.id]
    assert await db_session.get(RawMaterialAsset, source.id) is None


async def test_a_shared_raw_material_is_a_conflict_not_a_merge(db_session: AsyncSession) -> None:
    """A duplicate pair rolls the whole replace back; nothing is folded away."""
    source, target = await _assets(db_session)
    now = _now()
    shared = RawMaterial(name="ПРОБА-СЫРЬЁ-ОБЩЕЕ", created_at=now, updated_at=now)
    db_session.add(shared)
    await db_session.flush()
    db_session.add(AssetRawMaterial(asset_id=source.id, raw_material_id=shared.id, created_at=now, updated_at=now))
    db_session.add(AssetRawMaterial(asset_id=target.id, raw_material_id=shared.id, created_at=now, updated_at=now))
    await db_session.flush()
    source_id, target_id, material_id = source.id, target.id, shared.id
    await db_session.commit()

    with pytest.raises(ValueError, match="уникальности"):
        await replace_asset(db_session, asset_id=source_id, target_id=target_id)

    # The rollback restored the database state: both assets and both links.
    links = (
        await db_session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == material_id))
    ).all()
    assert sorted(link.asset_id for link in links) == sorted([source_id, target_id])
    assets = (
        await db_session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).like("ПРОБА-АКТИВ%")))
    ).all()
    assert len(assets) == 2
