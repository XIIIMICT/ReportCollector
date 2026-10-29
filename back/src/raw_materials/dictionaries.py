"""Raw-material reference dictionaries and asset-link lifecycle."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    RawMaterialUserAsset,
)
from src.raw_materials.schemas import AssetRowRead, DeliveryBasisRowRead, RawMaterialRowRead

from .errors import CellValidationError, DictionaryReferenceError

if TYPE_CHECKING:
    from src.auth.models import User

_DICT_REF_RAW_MATERIAL_NOT_FOUND = ValueError("Сырьевая позиция не найдена.")
_DICT_REF_RAW_MATERIAL_USED = ValueError("Нельзя удалить: позиция используется в формах «Движение» или «Цены».")
_DICT_REF_ASSET_NOT_FOUND = ValueError("Актив не найден.")
_DICT_REF_ASSET_USED = ValueError("Нельзя удалить: актив используется в формах «Движение» или «Цены».")
_DICT_REF_BASIS_NOT_FOUND = ValueError("Базис поставки не найден.")
_DICT_REF_BASIS_USED = ValueError("Нельзя удалить: базис используется в форме «Цены».")
_ERR_REPLACE_SELF = ValueError("Нельзя заменить значение на него же.")
_ERR_REPLACE_CONFLICT = ValueError(
    "Замена нарушила бы ограничения уникальности в связанных таблицах — ни одна запись не изменена.",
)


async def replace_raw_material(
    session: AsyncSession, *, raw_material_id: UUID, target_id: UUID | None
) -> list[AppliedCell]:
    """
    Re-point every «Движение»/«Цены» row of one item to another, then delete it.

    The asset links of the replaced item merge into the target: links that
    already hold movement or price rows stay (the moved rows join them), the
    empty ones are dropped. With `target_id=None` the item is simply deleted —
    its rows cannot survive without an item, and the delete refuses a used one.
    A uniqueness violation rolls the whole operation back.
    """
    source = await session.get(RawMaterial, raw_material_id)
    if source is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    if target_id is None:
        return await delete_raw_material(session, raw_material_id=raw_material_id)
    if target_id == raw_material_id:
        raise _ERR_REPLACE_SELF
    target = await session.get(RawMaterial, target_id)
    if target is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    source_links = (
        await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
    ).all()
    target_links = {
        link.asset_id: link
        for link in (
            await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == target_id))
        ).all()
    }
    for link in source_links:
        target_link = target_links.get(link.asset_id)
        if target_link is None:
            link.raw_material_id = target_id
            await session.flush()
            continue
        await session.exec(
            update(MaterialMovement)
            .where(col(MaterialMovement.asset_raw_material_id) == link.id)
            .values(asset_raw_material_id=target_link.id)
        )
        await session.exec(
            update(RawMaterialPrice)
            .where(col(RawMaterialPrice.asset_raw_material_id) == link.id)
            .values(asset_raw_material_id=target_link.id)
        )
        await session.delete(link)
        await session.flush()
    source_name = source.name
    target.updated_at = datetime.now(UTC)
    session.add(target)
    await session.delete(source)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise _ERR_REPLACE_CONFLICT from error
    return [
        AppliedCell(
            target_table=RawMaterial.__tablename__,
            row_id=raw_material_id,
            field="__replaced",
            old_value={"name": source_name, "targetId": str(target_id)},
            new_value=None,
        )
    ]


async def read_raw_material_rows(
    session: AsyncSession,
    *,
    raw_material_id: UUID | None = None,
    row_ids: list[UUID] | None = None,
) -> list[RawMaterialRowRead]:
    """«Сырьевые позиции» dictionary rows with assets."""
    query = (
        select(RawMaterial, RawMaterialAsset, AssetRawMaterial)
        .outerjoin(AssetRawMaterial, col(AssetRawMaterial.raw_material_id) == col(RawMaterial.id))
        .outerjoin(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .order_by(RawMaterial.name, RawMaterialAsset.name)
    )
    if raw_material_id is not None:
        query = query.where(col(RawMaterial.id) == raw_material_id)
    if row_ids is not None:
        query = query.where(col(RawMaterial.id).in_(row_ids))
    rows = (await session.exec(query)).all()
    grouped: dict[UUID, tuple[RawMaterial, list[str]]] = {}
    for raw_material, asset, _ in rows:
        entry = grouped.setdefault(raw_material.id, (raw_material, []))
        if asset is not None:
            entry[1].append(asset.name)
    return [
        RawMaterialRowRead(
            id=raw_material.id,
            name=raw_material.name,
            assetNames=names,
            createdAt=raw_material.created_at,
            updatedAt=raw_material.updated_at,
            updatedBy=raw_material.updated_by,
        )
        for raw_material, names in (entry for _, entry in grouped.items())
    ]


async def read_asset_rows(assets: list[RawMaterialAsset]) -> list[AssetRowRead]:
    """«Активы» dictionary rows within the user's access."""
    return [
        AssetRowRead(
            id=asset.id,
            name=asset.name,
            createdAt=asset.created_at,
            updatedAt=asset.updated_at,
            updatedBy=asset.updated_by,
        )
        for asset in assets
    ]


async def read_delivery_basis_rows(
    session: AsyncSession,
    *,
    delivery_basis_id: UUID | None = None,
    row_ids: list[UUID] | None = None,
) -> list[DeliveryBasisRowRead]:
    """«Базисы поставки» dictionary rows."""
    query = select(DeliveryBasis).order_by(DeliveryBasis.name)
    if delivery_basis_id is not None:
        query = query.where(col(DeliveryBasis.id) == delivery_basis_id)
    if row_ids is not None:
        query = query.where(col(DeliveryBasis.id).in_(row_ids))
    bases = (await session.exec(query)).all()
    return [
        DeliveryBasisRowRead(
            id=basis.id,
            name=basis.name,
            createdAt=basis.created_at,
            updatedAt=basis.updated_at,
            updatedBy=basis.updated_by,
        )
        for basis in bases
    ]


async def _arm_rows(
    session: AsyncSession, arm_ids: list[UUID]
) -> tuple[dict[UUID, list[MaterialMovement]], dict[UUID, RawMaterialPrice]]:
    """Group the links' «Движение»/«Цены» rows for the filled-data checks."""
    if not arm_ids:
        return {}, {}
    movement_rows = (
        await session.exec(select(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id).in_(arm_ids)))
    ).all()
    price_rows = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id).in_(arm_ids)))
    ).all()
    movements_by_arm: dict[UUID, list[MaterialMovement]] = {}
    for row in movement_rows:
        movements_by_arm.setdefault(row.asset_raw_material_id, []).append(row)
    prices_by_arm = {price.asset_raw_material_id: price for price in price_rows}
    return movements_by_arm, prices_by_arm


def _arm_rows_have_data(movements: list[MaterialMovement], price: RawMaterialPrice | None) -> bool:
    """Whether the link has filled «Движение»/«Цены» rows (empty ones don't count)."""
    movement_fields = (
        "demand_plan_tons",
        "consumption_fact_tons",
        "delivery_plan_tons",
        "delivery_fact_tons",
        "balance_start_tons",
        "balance_on_site_tons",
        "balance_in_transit_tons",
        "balance_end_tons",
        "deviation_reason",
    )
    for row in movements:
        if any(getattr(row, field) not in (None, "") for field in movement_fields):
            return True
    if price is None:
        return False
    price_fields = (
        "fact_2025",
        "business_plan_quantity_tons",
        "business_plan_usd_per_ton",
        "business_plan_rub_per_ton",
        "delivery_cost_rub",
        "storage_standard_tons",
    )
    return any(getattr(price, field) is not None for field in price_fields)


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def create_raw_material(
    session: AsyncSession,
    *,
    name: str,
    asset_ids: list[UUID],
    user: User,
) -> RawMaterial:
    raw_material = RawMaterial(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updater_user_id=user.id,
    )
    session.add(raw_material)
    await session.flush()
    await replace_asset_links(session, raw_material.id, asset_ids)
    await session.flush()
    return raw_material


async def _raw_material_asset_ids(session: AsyncSession, raw_material_id: UUID) -> list[UUID]:
    rows = (
        await session.exec(
            select(col(AssetRawMaterial.asset_id)).where(col(AssetRawMaterial.raw_material_id) == raw_material_id)
        )
    ).all()
    return [UUID(str(row)) for row in rows]


async def _asset_names_by_ids(session: AsyncSession, asset_ids: list[UUID]) -> list[str]:
    if not asset_ids:
        return []
    rows = (await session.exec(select(RawMaterialAsset.name).where(col(RawMaterialAsset.id).in_(asset_ids)))).all()
    return list(rows)


async def _asset_ids_by_names(session: AsyncSession, names: list[str]) -> list[UUID]:
    """Asset ids by names (for assetNames undo/redo rollback)."""
    if not names:
        return []
    rows = (await session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).in_(names)))).all()
    by_name = {row.name: row.id for row in rows}
    missing = [name for name in names if name not in by_name]
    if missing:
        message = f"Активы не найдены: {', '.join(missing)}"
        raise CellValidationError(message)
    return [by_name[name] for name in names]


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def update_raw_material(
    session: AsyncSession,
    *,
    raw_material_id: UUID,
    name: str | None,
    asset_ids: list[UUID] | None,
    user: User,
) -> tuple[RawMaterial, list[AppliedCell]]:
    raw_material = await session.get(RawMaterial, raw_material_id)
    if raw_material is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    applied: list[AppliedCell] = []
    old_name = raw_material.name
    old_asset_names = await _asset_names_by_ids(session, await _raw_material_asset_ids(session, raw_material_id))
    new_name = name.strip() if name is not None else old_name
    new_asset_names = await _asset_names_by_ids(session, asset_ids) if asset_ids is not None else old_asset_names
    if name is not None:
        raw_material.name = new_name
    if asset_ids is not None:
        await replace_asset_links(session, raw_material.id, asset_ids)
    raw_material.updated_at = datetime.now(UTC)
    raw_material.updater_user_id = user.id
    session.add(raw_material)
    await session.flush()
    if new_name != old_name:
        applied.append(
            AppliedCell(
                target_table=RawMaterial.__tablename__,
                row_id=raw_material.id,
                field="name",
                old_value=old_name,
                new_value=new_name,
            )
        )
    if new_asset_names != old_asset_names:
        applied.append(
            AppliedCell(
                target_table=RawMaterial.__tablename__,
                row_id=raw_material.id,
                field="assetNames",
                old_value=old_asset_names,
                new_value=new_asset_names,
            )
        )
    return raw_material, applied


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def delete_raw_material(session: AsyncSession, *, raw_material_id: UUID) -> list[AppliedCell]:
    """
    Delete a raw-material item and journal it as an undoable «__deleted» cell.

    The snapshot keeps the row and the assets it was linked to, so the undo
    restores both (the links carry no data — an item is only deletable while
    its movement/price rows are empty).
    """
    raw_material = await session.get(RawMaterial, raw_material_id)
    if raw_material is None:
        raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
    links = (
        await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
    ).all()
    movements_by_arm, prices_by_arm = await _arm_rows(session, [link.id for link in links])
    for link in links:
        if _arm_rows_have_data(movements_by_arm.get(link.id, []), prices_by_arm.get(link.id)):
            raise _DICT_REF_RAW_MATERIAL_USED
        await _delete_arm_rows(session, link.id)
        await session.delete(link)
    snapshot = {
        "name": raw_material.name,
        "assetIds": [str(link.asset_id) for link in links],
        "createdAt": raw_material.created_at.isoformat(),
        "updatedAt": raw_material.updated_at.isoformat(),
        "updatedBy": raw_material.updated_by,
    }
    await session.delete(raw_material)
    await session.flush()
    return [
        AppliedCell(
            target_table=RawMaterial.__tablename__,
            row_id=raw_material_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def create_asset(session: AsyncSession, *, name: str, user: User) -> RawMaterialAsset:
    asset = RawMaterialAsset(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updater_user_id=user.id,
    )
    session.add(asset)
    await session.flush()
    return asset


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def update_asset(
    session: AsyncSession,
    *,
    asset_id: UUID,
    name: str | None,
    user: User,
) -> tuple[RawMaterialAsset, list[AppliedCell]]:
    asset = await session.get(RawMaterialAsset, asset_id)
    if asset is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    old_name = asset.name
    new_name = name.strip() if name is not None else old_name
    if name is not None:
        asset.name = new_name
    asset.updated_at = datetime.now(UTC)
    asset.updater_user_id = user.id
    session.add(asset)
    await session.flush()
    applied: list[AppliedCell] = []
    if new_name != old_name:
        applied.append(
            AppliedCell(
                target_table=RawMaterialAsset.__tablename__,
                row_id=asset.id,
                field="name",
                old_value=old_name,
                new_value=new_name,
            )
        )
    return asset, applied


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def delete_asset(session: AsyncSession, *, asset_id: UUID) -> list[AppliedCell]:
    """
    Delete an asset and journal it as an undoable «__deleted» cell.

    The snapshot keeps the row and its per-user grants; the undo restores both.
    """
    asset = await session.get(RawMaterialAsset, asset_id)
    if asset is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    links = (await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.asset_id) == asset_id))).all()
    movements_by_arm, prices_by_arm = await _arm_rows(session, [link.id for link in links])
    for link in links:
        if _arm_rows_have_data(movements_by_arm.get(link.id, []), prices_by_arm.get(link.id)):
            raise _DICT_REF_ASSET_USED
        await _delete_arm_rows(session, link.id)
        await session.delete(link)
    user_links = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.asset_id) == asset_id))
    ).all()
    snapshot = {
        "name": asset.name,
        "userLinks": [str(link.user_id) for link in user_links],
        "createdAt": asset.created_at.isoformat(),
        "updatedAt": asset.updated_at.isoformat(),
        "updatedBy": asset.updated_by,
    }
    for link in user_links:
        await session.delete(link)
    await session.delete(asset)
    try:
        await session.flush()
    except IntegrityError as error:
        # A reference slipped in between the reference check and the commit
        # (a race) — surface a clean 409, never a 500.
        await session.rollback()
        raise _DICT_REF_ASSET_USED from error
    return [
        AppliedCell(
            target_table=RawMaterialAsset.__tablename__,
            row_id=asset_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def create_delivery_basis(session: AsyncSession, *, name: str, user: User) -> DeliveryBasis:
    basis = DeliveryBasis(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updater_user_id=user.id,
    )
    session.add(basis)
    await session.flush()
    return basis


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def update_delivery_basis(
    session: AsyncSession,
    *,
    basis_id: UUID,
    name: str | None,
    user: User,
) -> tuple[DeliveryBasis, list[AppliedCell]]:
    basis = await session.get(DeliveryBasis, basis_id)
    if basis is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    old_name = basis.name
    new_name = name.strip() if name is not None else old_name
    if name is not None:
        basis.name = new_name
    basis.updated_at = datetime.now(UTC)
    basis.updater_user_id = user.id
    session.add(basis)
    await session.flush()
    applied: list[AppliedCell] = []
    if new_name != old_name:
        applied.append(
            AppliedCell(
                target_table=DeliveryBasis.__tablename__,
                row_id=basis.id,
                field="name",
                old_value=old_name,
                new_value=new_name,
            )
        )
    return basis, applied


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def delete_delivery_basis(session: AsyncSession, *, basis_id: UUID) -> list[AppliedCell]:
    """Delete a delivery basis and journal it as an undoable «__deleted» cell."""
    basis = await session.get(DeliveryBasis, basis_id)
    if basis is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    price = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.delivery_basis_id) == basis_id))
    ).first()
    if price is not None:
        raise _DICT_REF_BASIS_USED
    snapshot = {
        "name": basis.name,
        "createdAt": basis.created_at.isoformat(),
        "updatedAt": basis.updated_at.isoformat(),
        "updatedBy": basis.updated_by,
    }
    await session.delete(basis)
    try:
        await session.flush()
    except IntegrityError as error:
        # A reference slipped in between the check and the commit (a race), or a
        # table we don't pre-check references the basis — surface a clean 409,
        # never a 500.
        await session.rollback()
        raise _DICT_REF_BASIS_USED from error
    return [
        AppliedCell(
            target_table=DeliveryBasis.__tablename__,
            row_id=basis_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def replace_asset(session: AsyncSession, *, asset_id: UUID, target_id: UUID | None) -> list[AppliedCell]:
    """
    Re-point one «Сырьё» asset's links to another, then delete the replaced one.

    Links, their «Движение»/«Цены» rows and the per-user grants move over
    unchanged. A raw material already linked to the target breaks the unique
    (asset, raw material) pair: nothing is merged, the whole replacement is
    rolled back and the usual uniqueness error is raised, so no link is
    silently folded away. With `target_id=None` the asset is deleted as usual.
    """
    source = await session.get(RawMaterialAsset, asset_id)
    if source is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    if target_id is None:
        return await delete_asset(session, asset_id=asset_id)
    if target_id == asset_id:
        raise _ERR_REPLACE_SELF
    target = await session.get(RawMaterialAsset, target_id)
    if target is None:
        raise _DICT_REF_ASSET_NOT_FOUND
    source_name = source.name
    try:
        for link in (
            await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.asset_id) == asset_id))
        ).all():
            link.asset_id = target_id
            await session.flush()
        for grant in (
            await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.asset_id) == asset_id))
        ).all():
            grant.asset_id = target_id
            await session.flush()
        target.updated_at = datetime.now(UTC)
        session.add(target)
        await session.delete(source)
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise _ERR_REPLACE_CONFLICT from error
    return [
        AppliedCell(
            target_table=RawMaterialAsset.__tablename__,
            row_id=asset_id,
            field="__replaced",
            old_value={"name": source_name, "targetId": str(target_id)},
            new_value=None,
        )
    ]


async def replace_delivery_basis(session: AsyncSession, *, basis_id: UUID, target_id: UUID | None) -> list[AppliedCell]:
    """
    Re-point every «Цены» row of one basis to another, then delete it.

    With `target_id=None` the basis is deleted as usual (refused while it is
    already used by a price row).
    """
    source = await session.get(DeliveryBasis, basis_id)
    if source is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    if target_id is None:
        return await delete_delivery_basis(session, basis_id=basis_id)
    if target_id == basis_id:
        raise _ERR_REPLACE_SELF
    target = await session.get(DeliveryBasis, target_id)
    if target is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    await session.exec(
        update(RawMaterialPrice)
        .where(col(RawMaterialPrice.delivery_basis_id) == basis_id)
        .values(delivery_basis_id=target_id)
    )
    source_name = source.name
    target.updated_at = datetime.now(UTC)
    session.add(target)
    await session.delete(source)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise _ERR_REPLACE_CONFLICT from error
    return [
        AppliedCell(
            target_table=DeliveryBasis.__tablename__,
            row_id=basis_id,
            field="__replaced",
            old_value={"name": source_name, "targetId": str(target_id)},
            new_value=None,
        )
    ]


async def _default_basis_id(session: AsyncSession) -> UUID | None:
    """Default basis for new «Цены» rows (first alphabetically, like in the seed)."""
    basis = (await session.exec(select(DeliveryBasis).order_by(DeliveryBasis.name))).first()
    return basis.id if basis is not None else None


async def replace_asset_links(session: AsyncSession, raw_material_id: UUID, asset_ids: list[UUID]) -> None:
    """Replace asset-raw-material links for a raw-material item (by diff)."""
    existing = {
        link.asset_id: link
        for link in (
            await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
        ).all()
    }
    existing_ids = set(existing)
    new_ids = set(asset_ids)
    # Remove only the dropped assets. Empty «Движение»/«Цены» rows are deleted
    # with the link; an asset with filled data can't be dropped — a business
    # rule, and the RESTRICT key is a guard against races.
    removed_ids = existing_ids - new_ids
    if removed_ids:
        blocked_ids: list[UUID] = []
        removed_links = [existing[asset_id] for asset_id in sorted(removed_ids)]
        movements_by_arm, prices_by_arm = await _arm_rows(session, [link.id for link in removed_links])
        for link in removed_links:
            if _arm_rows_have_data(movements_by_arm.get(link.id, []), prices_by_arm.get(link.id)):
                blocked_ids.append(link.asset_id)
            else:
                await _delete_arm_rows(session, link.id)
                await session.delete(link)
        if blocked_ids:
            names = await _asset_names_by_ids(session, sorted(blocked_ids))
            raise DictionaryReferenceError("Нельзя убрать активы, по которым есть данные движения: " + ", ".join(names))
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        names = await _asset_names_by_ids(session, sorted(removed_ids))
        raise DictionaryReferenceError(
            "Нельзя убрать активы, по которым есть данные движения: " + ", ".join(names)
        ) from error
    now = datetime.now(UTC)
    new_links: list[AssetRawMaterial] = []
    for asset_id in asset_ids:
        if asset_id not in existing_ids:
            link = AssetRawMaterial(
                asset_id=asset_id,
                raw_material_id=raw_material_id,
                created_at=now,
                updated_at=now,
            )
            session.add(link)
            new_links.append(link)
    await session.flush()
    if new_links:
        # A new link immediately yields «Цены» and «Движение» rows (like seeding):
        # one price per link, one movement row per open report month.
        basis_id = await _default_basis_id(session)
        periods = [row for row in (await session.exec(select(col(MovementPeriod.report_month)))).all()]
        for link in new_links:
            session.add(
                RawMaterialPrice(
                    asset_raw_material_id=link.id,
                    delivery_basis_id=basis_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            for report_month in periods:
                session.add(
                    MaterialMovement(
                        asset_raw_material_id=link.id,
                        report_month=report_month,
                        created_at=now,
                        updated_at=now,
                    )
                )
        await session.flush()


async def _delete_arm_rows(session: AsyncSession, arm_id: UUID) -> None:
    """Delete the link's (empty) «Движение» and «Цены» rows before deleting the link itself."""
    await session.exec(delete(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id) == arm_id))
    await session.exec(delete(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id) == arm_id))
