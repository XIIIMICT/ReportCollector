"""
Read service for the «Сырьё» (raw materials) project (read-only).

Queries mirror the Tauri app logic (domain.rs): «Движение» and «Цены»
form rows sorted by raw-material name and asset.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select as sqlalchemy_select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import Select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.schemas import AssetAccessRead
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.locking import lock_project, lock_rows, project_write
from src.collab.models import MOVEMENT_TABLE, PRICE_TABLE
from src.collab.persistence import JournalStore
from src.collab.store import get_collab_store, lease_key
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    RawMaterialUserAsset,
    ReportSnapshot,
    SnapshotMaterialMovement,
)
from src.raw_materials.schemas import (
    AssetOptionRead,
    AssetRowRead,
    CellUpdate,
    DeliveryBasisRowRead,
    MovementRowRead,
    MovementSnapshotRead,
    PriceRowRead,
    RawMaterialRowRead,
    RawMaterialsMetaRead,
)

from .errors import DICT_CREATED_SNAPSHOT_INVALID as _DICT_CREATED_SNAPSHOT_INVALID
from .errors import CellValidationError, DictionaryReferenceError
from .users import _DICT_REF_USER_NOT_FOUND, _apply_deleted_user, replace_user_assets

# SQLModel's select() overloads support up to 4 entities; for the 5-entity
# form queries we use a typed cast over SQLAlchemy.
# sqlmodel 0.0.48 made Select variadic: Select[A, B, ...] (one type param per entity).
MovementRowsSelect = Select[MaterialMovement, AssetRawMaterial, RawMaterialAsset, RawMaterial, RawMaterialPrice]
PriceRowsSelect = Select[RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis]


class NoRawMaterialsAccessError(Exception):
    """The user has no access to the «Сырьё» project."""


async def resolve_raw_materials_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
    *,
    project_key: str = RAW_MATERIALS_PROJECT_KEY,
) -> tuple[User, list[ProjectRole], list[RawMaterialAsset]]:
    """
    Return (user, roles, available assets) of the project.

    project_key allows checking access to another project of the same
    mechanics (e.g. for collaboration rooms). A project admin sees all
    assets; an asset worker only the ones assigned to them. Raises
    NoRawMaterialsAccessError if the user has no role in the project.
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoRawMaterialsAccessError

    roles = list(
        (
            await session.exec(
                select(col(UserRole.role))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(
                    col(UserRole.user_id) == user.id,
                    col(Project.key) == project_key,
                    col(UserRole.is_active).is_(True),
                )
            )
        ).all()
    )
    if not roles:
        raise NoRawMaterialsAccessError

    if ProjectRole.ADMIN in roles:
        assets = (await session.exec(select(RawMaterialAsset).order_by(RawMaterialAsset.name))).all()
    else:
        assets = (
            await session.exec(
                select(RawMaterialAsset)
                .join(RawMaterialUserAsset, col(RawMaterialUserAsset.asset_id) == col(RawMaterialAsset.id))
                .where(RawMaterialUserAsset.user_id == user.id)
                .order_by(RawMaterialAsset.name)
            )
        ).all()
    return user, roles, list(assets)


def accessible_assets_read(assets: list[RawMaterialAsset]) -> list[AssetAccessRead]:
    return [AssetAccessRead(name=asset.name) for asset in assets]


async def read_meta(session: AsyncSession, assets: list[RawMaterialAsset]) -> RawMaterialsMetaRead:
    periods = (
        await session.exec(select(col(MovementPeriod.report_month)).order_by(col(MovementPeriod.report_month)))
    ).all()
    bases = (await session.exec(select(DeliveryBasis.name).order_by(DeliveryBasis.name))).all()
    return RawMaterialsMetaRead(
        assets=[AssetOptionRead(id=asset.id, name=asset.name) for asset in assets],
        movementPeriods=list(periods),
        deliveryBases=list(bases),
    )


async def read_movement_versions(session: AsyncSession) -> list[ReportSnapshot]:
    """
    All movement snapshots across every report month (final/preliminary).

    Feeds the global «Версия…» picker shown on the first movement tab: one item
    per month that was ever fixed — closed months render as final, an open month
    currently preview-fixed renders as its preliminary snapshot. Newest month on top.
    """
    return list((await session.exec(select(ReportSnapshot).order_by(col(ReportSnapshot.report_month).desc()))).all())


async def read_movement_rows(
    session: AsyncSession,
    report_month: date,
    assets: list[RawMaterialAsset],
) -> list[MovementRowRead]:
    """«Движение» form rows for the report month (like read_current_movements in Tauri)."""
    query: MovementRowsSelect = cast(
        "MovementRowsSelect",
        sqlalchemy_select(
            MaterialMovement,
            AssetRawMaterial,
            RawMaterialAsset,
            RawMaterial,
            RawMaterialPrice,
        )
        .join(AssetRawMaterial, col(AssetRawMaterial.id) == col(MaterialMovement.asset_raw_material_id))
        .join(RawMaterialPrice, col(RawMaterialPrice.asset_raw_material_id) == col(AssetRawMaterial.id))
        .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .join(RawMaterial, col(RawMaterial.id) == col(AssetRawMaterial.raw_material_id))
        .where(col(MaterialMovement.report_month) == report_month)
        .order_by(RawMaterial.name, RawMaterialAsset.name, col(AssetRawMaterial.id)),
    )
    query = query.where(col(RawMaterialAsset.id).in_([asset.id for asset in assets]))

    rows = []
    for movement, arm, asset, raw_material, price in (await session.exec(query)).all():
        updated_at, updated_by = movement_audit(movement, price)
        rows.append(
            MovementRowRead(
                id=movement.id,
                key=arm.id,
                assetId=asset.id,
                assetName=asset.name,
                rawMaterialId=raw_material.id,
                rawMaterialName=raw_material.name,
                businessPlanTons=price.business_plan_quantity_tons,
                demandPlanTons=movement.demand_plan_tons,
                consumptionFactTons=movement.consumption_fact_tons,
                demandDeviationTons=calculate_deviation(movement.demand_plan_tons, movement.consumption_fact_tons),
                deliveryPlanTons=movement.delivery_plan_tons,
                deliveryFactTons=movement.delivery_fact_tons,
                deliveryDeviationTons=calculate_deviation(movement.delivery_plan_tons, movement.delivery_fact_tons),
                balanceStartTons=movement.balance_start_tons,
                balanceOnSiteTons=movement.balance_on_site_tons,
                balanceInTransitTons=movement.balance_in_transit_tons,
                balanceEndTons=movement.balance_end_tons,
                storageStandardTons=price.storage_standard_tons,
                deviationReason=movement.deviation_reason,
                createdAt=arm.created_at,
                updatedAt=updated_at,
                updatedBy=updated_by,
                priceId=price.id,
            )
        )
    return rows


async def read_snapshot_export_rows(
    session: AsyncSession,
    snapshot_id: UUID,
    assets: list[RawMaterialAsset],
) -> tuple[MovementSnapshotRead | None, list[MovementRowRead]]:
    """Archive «Движение» version rows as MovementRowRead for export."""
    snapshot = await session.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        return None, []
    rows = (
        await session.exec(
            select(SnapshotMaterialMovement)
            .where(col(SnapshotMaterialMovement.snapshot_id) == snapshot_id)
            .where(col(SnapshotMaterialMovement.source_asset_id).in_([asset.id for asset in assets]))
            .order_by(
                SnapshotMaterialMovement.raw_material_name,
                SnapshotMaterialMovement.asset_name,
            )
        )
    ).all()
    return (
        MovementSnapshotRead(
            id=snapshot.id,
            reportMonth=snapshot.report_month,
            fixedAt=snapshot.fixed_at,
            fixedByFullname=snapshot.fixed_by_fullname,
            isFinal=snapshot.is_final,
        ),
        [
            MovementRowRead(
                id=row.source_asset_raw_material_id,
                key=row.source_asset_raw_material_id,
                assetId=row.source_asset_id,
                assetName=row.asset_name,
                rawMaterialId=row.source_raw_material_id,
                rawMaterialName=row.raw_material_name,
                businessPlanTons=row.business_plan_tons,
                demandPlanTons=row.demand_plan_tons,
                consumptionFactTons=row.consumption_fact_tons,
                demandDeviationTons=row.demand_deviation_tons,
                deliveryPlanTons=row.delivery_plan_tons,
                deliveryFactTons=row.delivery_fact_tons,
                deliveryDeviationTons=row.delivery_deviation_tons,
                balanceStartTons=row.balance_start_tons,
                balanceOnSiteTons=row.balance_on_site_tons,
                balanceInTransitTons=row.balance_in_transit_tons,
                balanceEndTons=row.balance_end_tons,
                storageStandardTons=row.storage_standard_tons,
                deviationReason=row.deviation_reason,
                createdAt=snapshot.fixed_at,
                updatedAt=snapshot.fixed_at,
                updatedBy=snapshot.fixed_by_fullname,
                priceId=row.source_asset_raw_material_id,
            )
            for row in rows
        ],
    )


async def read_price_rows(
    session: AsyncSession,
    assets: list[RawMaterialAsset],
) -> list[PriceRowRead]:
    """«Цены» form rows (like read_current_prices in Tauri)."""
    query: PriceRowsSelect = cast(
        "PriceRowsSelect",
        sqlalchemy_select(RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis)
        .join(AssetRawMaterial, col(AssetRawMaterial.id) == col(RawMaterialPrice.asset_raw_material_id))
        .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(AssetRawMaterial.asset_id))
        .join(RawMaterial, col(RawMaterial.id) == col(AssetRawMaterial.raw_material_id))
        .join(DeliveryBasis, col(DeliveryBasis.id) == col(RawMaterialPrice.delivery_basis_id), isouter=True)
        .order_by(RawMaterial.name, RawMaterialAsset.name, col(AssetRawMaterial.id)),
    )
    query = query.where(col(RawMaterialAsset.id).in_([asset.id for asset in assets]))

    rows = []
    for price, arm, asset, raw_material, basis in (await session.exec(query)).all():
        rows.append(
            PriceRowRead(
                id=price.id,
                key=arm.id,
                assetId=asset.id,
                assetName=asset.name,
                rawMaterialId=raw_material.id,
                rawMaterialName=raw_material.name,
                deliveryBasisName=basis.name if basis else None,
                fact2025=price.fact_2025,
                businessPlanQuantityTons=price.business_plan_quantity_tons,
                businessPlanUsdPerTon=price.business_plan_usd_per_ton,
                businessPlanRubPerTon=price.business_plan_rub_per_ton,
                deliveryCostRub=price.delivery_cost_rub,
                storageStandardTons=price.storage_standard_tons,
                createdAt=arm.created_at,
                updatedAt=price.updated_at,
                updatedBy=price.updated_by,
            )
        )
    return rows


async def read_raw_material_rows(
    session: AsyncSession,
    *,
    raw_material_id: UUID | None = None,
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
) -> list[DeliveryBasisRowRead]:
    """«Базисы поставки» dictionary rows."""
    query = select(DeliveryBasis).order_by(DeliveryBasis.name)
    if delivery_basis_id is not None:
        query = query.where(col(DeliveryBasis.id) == delivery_basis_id)
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


def movement_audit(
    movement: MaterialMovement,
    price: RawMaterialPrice,
) -> tuple[datetime, str]:
    """«Движение» row audit: takes the freshest edit from movement or prices."""
    candidates: list[tuple[datetime, str]] = [(movement.updated_at, movement.updated_by)]
    if price.business_plan_updated_at is not None:
        candidates.append((price.business_plan_updated_at, price.business_plan_updated_by))
    if price.storage_standard_updated_at is not None:
        candidates.append((price.storage_standard_updated_at, price.storage_standard_updated_by))
    return max(candidates, key=lambda c: c[0])


def calculate_deviation(plan: float | None, fact: float | None) -> float | None:
    if plan is None or fact is None:
        return None
    return plan - fact


# ─── Write: cell edits of the «Движение» and «Цены» forms ──────────────

# «Движение» form fields written into MaterialMovement.
# Typical write-error messages (module constants — project style).
_CELL_MISSING_ROW_OR_FIELD = ValueError("Ячейка не содержит идентификатор строки или поля.")
_CELL_MOVEMENT_NOT_FOUND = ValueError("Строка «Движения» не найдена.")
_CELL_MOVEMENT_NO_ACCESS = ValueError("Нет доступа к строке «Движения».")
_CELL_MOVEMENT_NO_PRICE = ValueError("Для строки «Движения» не найдена цена.")
_CELL_PRICE_NOT_FOUND = ValueError("Строка «Цен» не найдена.")
_CELL_PRICE_NO_ACCESS = ValueError("Нет доступа к строке «Цен».")
_DICT_REF_RAW_MATERIAL_NOT_FOUND = ValueError("Сырьевая позиция не найдена.")
_DICT_REF_RAW_MATERIAL_USED = ValueError("Нельзя удалить: позиция используется в формах «Движение» или «Цены».")
_DICT_REF_ASSET_NOT_FOUND = ValueError("Актив не найден.")
_DICT_REF_ASSET_USED = ValueError("Нельзя удалить: актив используется в формах «Движение» или «Цены».")
_DICT_REF_BASIS_NOT_FOUND = ValueError("Базис поставки не найден.")
_DICT_REF_BASIS_USED = ValueError("Нельзя удалить: базис используется в форме «Цены».")
_DICT_REF_USER_EXISTS = ValueError("Пользователь с таким email уже существует.")
_DICT_REF_ASSETS_NOT_FOUND = ValueError("Один или несколько активов не найдены.")

MOVEMENT_CELL_FIELDS: dict[str, str] = {
    "demandPlanTons": "demand_plan_tons",
    "consumptionFactTons": "consumption_fact_tons",
    "deliveryPlanTons": "delivery_plan_tons",
    "deliveryFactTons": "delivery_fact_tons",
    "balanceStartTons": "balance_start_tons",
    "balanceOnSiteTons": "balance_on_site_tons",
    "balanceInTransitTons": "balance_in_transit_tons",
    "balanceEndTons": "balance_end_tons",
    "deviationReason": "deviation_reason",
}

# «Движение» form fields written into RawMaterialPrice (business plan, storage standard).
MOVEMENT_PRICE_FIELDS: dict[str, str] = {
    "businessPlanTons": "business_plan_quantity_tons",
    "storageStandardTons": "storage_standard_tons",
}

# «Цены» form fields written into RawMaterialPrice.
PRICE_CELL_FIELDS: dict[str, str] = {
    "fact2025": "fact_2025",
    "businessPlanQuantityTons": "business_plan_quantity_tons",
    "businessPlanUsdPerTon": "business_plan_usd_per_ton",
    "businessPlanRubPerTon": "business_plan_rub_per_ton",
    "deliveryCostRub": "delivery_cost_rub",
    "storageStandardTons": "storage_standard_tons",
}


@project_write(RAW_MATERIALS_PROJECT_KEY)
async def update_movement_cells(
    session: AsyncSession,
    *,
    report_month: date,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
    session_id: str,
) -> list[AppliedCell]:
    """Apply «Движение» form cell edits and write the journal (same transaction)."""
    await lock_project(session, RAW_MATERIALS_PROJECT_KEY)
    await lock_rows(session, [f"undo/{user.id}/{session_id}/materialMovement/{report_month}"])
    await lock_rows(session, [f"{MOVEMENT_TABLE}/{update.rowId}" for update in updates])
    if await session.get(MovementPeriod, report_month) is None:
        message = "Отчётный месяц закрыт для редактирования."
        raise CellValidationError(message)
    await check_edit_leases(
        updates, user=user, session_id=session_id, table=MOVEMENT_TABLE, project=RAW_MATERIALS_PROJECT_KEY
    )
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    # Batch-load rows, links and prices up front: one query per table instead
    # of two-three per cell (a paste can carry hundreds of cells).
    row_ids = [update.rowId for update in updates if update.rowId is not None]
    movements = {
        movement.id: movement
        for movement in (
            await session.exec(
                select(MaterialMovement)
                .where(col(MaterialMovement.id).in_(row_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    arms = await _arms_by_id(session, {movement.asset_raw_material_id for movement in movements.values()})
    prices = (
        await _prices_by_arm(session, set(arms))
        if any(update.field in MOVEMENT_PRICE_FIELDS for update in updates)
        else {}
    )
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        movement = movements.get(row_id)
        if movement is None or movement.report_month != report_month:
            raise _CELL_MOVEMENT_NOT_FOUND
        arm = arms.get(movement.asset_raw_material_id)
        if arm is None or arm.asset_id not in asset_ids:
            raise _CELL_MOVEMENT_NO_ACCESS
        if field in MOVEMENT_CELL_FIELDS:
            column = MOVEMENT_CELL_FIELDS[field]
            old_value = getattr(movement, column)
            new_value = coerce_number_or_text(value, field)
            if new_value == old_value:
                continue
            setattr(movement, column, new_value)
        elif field in MOVEMENT_PRICE_FIELDS:
            price = prices.get(movement.asset_raw_material_id)
            if price is None:
                raise _CELL_MOVEMENT_NO_PRICE
            column = MOVEMENT_PRICE_FIELDS[field]
            old_value = getattr(price, column)
            new_value = coerce_number_or_text(value, field)
            if new_value == old_value:
                continue
            setattr(price, column, new_value)
            if field == "businessPlanTons":
                price.business_plan_updated_at = datetime.now(UTC)
                price.business_plan_updated_by = user.name or user.email or ""
            elif field == "storageStandardTons":
                price.storage_standard_updated_at = datetime.now(UTC)
                price.storage_standard_updated_by = user.name or user.email or ""
            session.add(price)
        else:
            message = f"Неизвестное поле «{field}» формы «Движение»."
            raise CellValidationError(message)
        movement.updated_at = datetime.now(UTC)
        movement.updated_by = user.name or user.email or ""
        session.add(movement)
        applied.append(
            AppliedCell(
                target_table=MOVEMENT_TABLE,
                row_id=row_id,
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
        )
    await record_cell_updates(
        JournalStore(session),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="materialMovement",
        report_month=report_month,
        session_id=session_id,
        applied=applied,
    )
    await session.flush()
    return applied


@project_write(RAW_MATERIALS_PROJECT_KEY)
async def update_price_cells(
    session: AsyncSession,
    *,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
    session_id: str,
) -> list[AppliedCell]:
    """Apply «Цены» form cell edits and write the journal (same transaction)."""
    await lock_project(session, RAW_MATERIALS_PROJECT_KEY)
    await lock_rows(session, [f"undo/{user.id}/{session_id}/rawMaterialPrices/"])
    await lock_rows(session, [f"{PRICE_TABLE}/{update.rowId}" for update in updates])
    await check_edit_leases(
        updates, user=user, session_id=session_id, table=PRICE_TABLE, project=RAW_MATERIALS_PROJECT_KEY
    )
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    # Batch-load prices and links up front (one query per table); the basis
    # dictionary is tiny, so it's loaded whole once for journal old-values.
    row_ids = [update.rowId for update in updates if update.rowId is not None]
    prices = {
        price.id: price
        for price in (
            await session.exec(
                select(RawMaterialPrice)
                .where(col(RawMaterialPrice.id).in_(row_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    arms = await _arms_by_id(session, {price.asset_raw_material_id for price in prices.values()})
    basis_names = {basis.id: basis.name for basis in (await session.exec(select(DeliveryBasis))).all()}
    resolved_bases: dict[str | None, UUID | None] = {}
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        price = prices.get(row_id)
        if price is None:
            raise _CELL_PRICE_NOT_FOUND
        arm = arms.get(price.asset_raw_material_id)
        if arm is None or arm.asset_id not in asset_ids:
            raise _CELL_PRICE_NO_ACCESS
        if field == "deliveryBasisName":
            old_value = basis_names.get(price.delivery_basis_id)
            new_value = coerce_text(value)
            if new_value in resolved_bases:
                price.delivery_basis_id = resolved_bases[new_value]
            else:
                basis_id = await resolve_basis_id(session, new_value)
                resolved_bases[new_value] = basis_id
                price.delivery_basis_id = basis_id
        elif field in PRICE_CELL_FIELDS:
            column = PRICE_CELL_FIELDS[field]
            old_value = getattr(price, column)
            new_value = coerce_number_or_text(value, field)
            setattr(price, column, new_value)
        else:
            message = f"Неизвестное поле «{field}» формы «Цены»."
            raise CellValidationError(message)
        price.updated_at = datetime.now(UTC)
        price.updated_by = user.name or user.email or ""
        session.add(price)
        applied.append(
            AppliedCell(
                target_table=PRICE_TABLE,
                row_id=row_id,
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
        )
    await record_cell_updates(
        JournalStore(session),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="rawMaterialPrices",
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await session.flush()
    return applied


class EditableCell(Protocol):
    rowId: UUID
    field: str


async def check_edit_leases(
    updates: Sequence[EditableCell], *, user: User, session_id: str, table: str, project: str
) -> None:
    """Reject another session's active lease, including direct HTTP writes."""
    keys = [lease_key(f"{table}/{update.rowId}/{update.field}", project=project) for update in updates]
    for owner in await get_collab_store().get_leases(keys):
        if owner is not None and (
            owner.get("userId") != str(user.id) or owner.get("sessionId", session_id) != session_id
        ):
            message = "Ячейка редактируется в другой сессии. Повторите после освобождения."
            raise CellValidationError(message)


async def apply_cell_value(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """Apply a normalized cell value (undo/redo) with audit update."""
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    if field == "__created":
        await _apply_created_record(session, target_table=target_table, row_id=row_id, snapshot=value, user=user)
        return
    if field == "__deleted":
        if target_table == User.__tablename__:
            await _apply_deleted_user(session, row_id=row_id, snapshot=value)
            return
        await _apply_deleted_record(session, target_table=target_table, row_id=row_id, snapshot=value)
        return
    if target_table == MOVEMENT_TABLE:
        movement = await session.get(MaterialMovement, row_id)
        if movement is None:
            raise _CELL_MOVEMENT_NOT_FOUND
        if field in MOVEMENT_PRICE_FIELDS:
            price = await _movement_price(session, movement)
            setattr(price, MOVEMENT_PRICE_FIELDS[field], value)
            if field == "businessPlanTons":
                price.business_plan_updated_at = now
                price.business_plan_updated_by = updated_by
            elif field == "storageStandardTons":
                price.storage_standard_updated_at = now
                price.storage_standard_updated_by = updated_by
            session.add(price)
        elif field in MOVEMENT_CELL_FIELDS:
            setattr(movement, MOVEMENT_CELL_FIELDS[field], value)
        else:
            message = f"Неизвестное поле «{field}» формы «Движение»."
            raise CellValidationError(message)
        movement.updated_at = now
        movement.updated_by = updated_by
        session.add(movement)
        return
    if target_table == PRICE_TABLE:
        price = await session.get(RawMaterialPrice, row_id)
        if price is None:
            raise _CELL_PRICE_NOT_FOUND
        if field == "deliveryBasisName":
            price.delivery_basis_id = await resolve_basis_id(session, value if isinstance(value, str) else None)
        elif field in PRICE_CELL_FIELDS:
            setattr(price, PRICE_CELL_FIELDS[field], value)
        else:
            message = f"Неизвестное поле «{field}» формы «Цены»."
            raise CellValidationError(message)
        price.updated_at = now
        price.updated_by = updated_by
        session.add(price)
        return
    # Dictionaries: undo/redo of name and asset-set edits.
    if target_table == RawMaterial.__tablename__:
        raw_material = await session.get(RawMaterial, row_id)
        if raw_material is None:
            raise _DICT_REF_RAW_MATERIAL_NOT_FOUND
        if field == "name":
            raw_material.name = value if isinstance(value, str) else ""
        elif field == "assetNames":
            names = value if isinstance(value, list) else []
            await replace_asset_links(session, raw_material.id, await _asset_ids_by_names(session, names))
        else:
            message = f"Неизвестное поле «{field}» справочника «Сырьевые позиции»."
            raise CellValidationError(message)
        raw_material.updated_at = now
        raw_material.updated_by = updated_by
        session.add(raw_material)
        return
    if target_table == RawMaterialAsset.__tablename__:
        asset = await session.get(RawMaterialAsset, row_id)
        if asset is None:
            raise _DICT_REF_ASSET_NOT_FOUND
        asset.name = value if isinstance(value, str) else ""
        asset.updated_at = now
        asset.updated_by = updated_by
        session.add(asset)
        return
    if target_table == DeliveryBasis.__tablename__:
        basis = await session.get(DeliveryBasis, row_id)
        if basis is None:
            raise _DICT_REF_BASIS_NOT_FOUND
        basis.name = value if isinstance(value, str) else ""
        basis.updated_at = now
        basis.updated_by = updated_by
        session.add(basis)
        return
    if target_table == User.__tablename__:
        user_row = await session.get(User, row_id)
        if user_row is None or user_row.is_deleted:
            raise _DICT_REF_USER_NOT_FOUND
        project_id = (
            await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
        ).first()
        link = (
            await session.exec(select(UserRole).where(UserRole.user_id == row_id, UserRole.project_id == project_id))
        ).one_or_none()
        if field == "email":
            new_email = value if isinstance(value, str) and value.strip() else None
            if new_email is not None:
                user_row.email = new_email
        elif field == "fullname":
            user_row.name = value if isinstance(value, str) else ""
        elif field == "isAdmin":
            if link is None:
                raise _DICT_REF_USER_NOT_FOUND
            link.role = ProjectRole.ADMIN if value else ProjectRole.ASSET_WORKER
        elif field == "isActive":
            if link is None:
                raise _DICT_REF_USER_NOT_FOUND
            link.is_active = bool(value)
        elif field == "assetNames":
            await replace_user_assets(session, user_row.id, value if isinstance(value, list) else None)
        else:
            message = f"Неизвестное поле «{field}» справочника «Пользователи»."
            raise CellValidationError(message)
        user_row.updated_at = now
        session.add(user_row)
        if link is not None:
            link.updated_at = now
            link.updated_by = updated_by
            session.add(link)
        return
    message = f"Неизвестная таблица «{target_table}»."
    raise CellValidationError(message)


async def _apply_created_record(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    snapshot: object,
    user: User,
) -> None:
    """
    Undo/redo of a dictionary record creation.

    The create endpoints journal an «__created» pseudo-cell whose new value is
    the record snapshot. Undo (value=None) removes the created record with the
    same business rules as a manual delete; redo (value=snapshot) restores it
    with the original id. Users can't be deleted (the product closes access
    with the «Активен» flag instead), so their undo deactivates the project
    link and clears the asset list, and their redo restores role, activity
    and assets.

    Stale states are no-ops, not errors: undo of a record that was already
    deleted manually does nothing, and redo of a record that already exists
    again does nothing (the end state matches either way).
    """
    if target_table == User.__tablename__:
        project_id = (
            await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
        ).first()
        if project_id is None:
            raise _DICT_REF_USER_NOT_FOUND
        link = (
            await session.exec(select(UserRole).where(UserRole.user_id == row_id, UserRole.project_id == project_id))
        ).one_or_none()
        if snapshot is None:
            if link is None:
                return
            link.is_active = False
            link.updated_at = datetime.now(UTC)
            session.add(link)
            await replace_user_assets(session, row_id, [])
            await session.flush()
            return
        if not isinstance(snapshot, dict):
            raise _DICT_CREATED_SNAPSHOT_INVALID
        user_row = await session.get(User, row_id)
        if user_row is not None and user_row.is_deleted:
            raise _DICT_REF_USER_NOT_FOUND
        if link is None:
            now = datetime.now(UTC)
            link = UserRole(
                user_id=row_id,
                project_id=project_id,
                role=ProjectRole.ADMIN if snapshot.get("isAdmin") else ProjectRole.ASSET_WORKER,
                is_active=bool(snapshot.get("isActive", True)),
                created_at=now,
                updated_at=now,
            )
        else:
            link.role = ProjectRole.ADMIN if snapshot.get("isAdmin") else ProjectRole.ASSET_WORKER
            link.is_active = bool(snapshot.get("isActive", True))
            link.updated_at = datetime.now(UTC)
        session.add(link)
        asset_names = snapshot.get("assetNames")
        await replace_user_assets(session, row_id, asset_names if isinstance(asset_names, list) else [])
        await session.flush()
        return
    if snapshot is None:
        if target_table == RawMaterialAsset.__tablename__:
            if await session.get(RawMaterialAsset, row_id) is None:
                return
            await delete_asset(session, asset_id=row_id)
            return
        if target_table == DeliveryBasis.__tablename__:
            if await session.get(DeliveryBasis, row_id) is None:
                return
            await delete_delivery_basis(session, basis_id=row_id)
            return
        if target_table == RawMaterial.__tablename__:
            if await session.get(RawMaterial, row_id) is None:
                return
            await delete_raw_material(session, raw_material_id=row_id)
            return
        message = f"Неизвестная таблица «{target_table}»."
        raise CellValidationError(message)
    if not isinstance(snapshot, dict):
        raise _DICT_CREATED_SNAPSHOT_INVALID
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    name = snapshot.get("name")
    if target_table == RawMaterialAsset.__tablename__:
        if await session.get(RawMaterialAsset, row_id) is not None:
            return
        session.add(RawMaterialAsset(id=row_id, name=str(name), created_at=now, updated_at=now, updated_by=updated_by))
        return
    if target_table == DeliveryBasis.__tablename__:
        if await session.get(DeliveryBasis, row_id) is not None:
            return
        session.add(DeliveryBasis(id=row_id, name=str(name), created_at=now, updated_at=now, updated_by=updated_by))
        return
    if target_table == RawMaterial.__tablename__:
        if await session.get(RawMaterial, row_id) is not None:
            return
        raw_material = RawMaterial(id=row_id, name=str(name), created_at=now, updated_at=now, updated_by=updated_by)
        session.add(raw_material)
        await session.flush()
        raw_asset_ids = snapshot.get("assetIds")
        await replace_asset_links(
            session,
            raw_material.id,
            [UUID(item) for item in raw_asset_ids] if isinstance(raw_asset_ids, list) else [],
        )
        return
    message = f"Неизвестная таблица «{target_table}»."
    raise CellValidationError(message)


async def _apply_deleted_record(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    snapshot: object,
) -> None:
    """
    Undo/redo of a dictionary record deletion.

    The delete endpoints journal an «__deleted» pseudo-cell whose old value is
    the record snapshot and whose new value is `None`. Undo (value=snapshot)
    restores the row with its original id and links; redo (value=None) deletes
    it again through the same service path, so the business rules (a row used
    by «Движение»/«Цены» can't be deleted) still apply.

    A stale state is a no-op, not an error: restoring an existing row does
    nothing, deleting an already-deleted one does nothing.
    """
    if snapshot is None:
        if target_table == RawMaterialAsset.__tablename__:
            if await session.get(RawMaterialAsset, row_id) is None:
                return
            await delete_asset(session, asset_id=row_id)
            return
        if target_table == DeliveryBasis.__tablename__:
            if await session.get(DeliveryBasis, row_id) is None:
                return
            await delete_delivery_basis(session, basis_id=row_id)
            return
        if target_table == RawMaterial.__tablename__:
            if await session.get(RawMaterial, row_id) is None:
                return
            await delete_raw_material(session, raw_material_id=row_id)
            return
        message = f"Неизвестная таблица «{target_table}»."
        raise CellValidationError(message)
    if not isinstance(snapshot, dict):
        raise _DICT_CREATED_SNAPSHOT_INVALID
    name = snapshot.get("name")
    created_at = _snapshot_datetime(snapshot.get("createdAt"))
    updated_at = _snapshot_datetime(snapshot.get("updatedAt")) or created_at
    updated_by = str(snapshot.get("updatedBy") or "")
    if target_table == RawMaterialAsset.__tablename__:
        if await session.get(RawMaterialAsset, row_id) is not None:
            return
        session.add(
            RawMaterialAsset(
                id=row_id,
                name=str(name),
                created_at=created_at,
                updated_at=updated_at,
                updated_by=updated_by,
            )
        )
        for user_id in snapshot.get("userLinks") or []:
            session.add(RawMaterialUserAsset(user_id=UUID(str(user_id)), asset_id=row_id))
        return
    if target_table == DeliveryBasis.__tablename__:
        if await session.get(DeliveryBasis, row_id) is not None:
            return
        session.add(
            DeliveryBasis(
                id=row_id,
                name=str(name),
                created_at=created_at,
                updated_at=updated_at,
                updated_by=updated_by,
            )
        )
        return
    if target_table == RawMaterial.__tablename__:
        if await session.get(RawMaterial, row_id) is not None:
            return
        session.add(
            RawMaterial(
                id=row_id,
                name=str(name),
                created_at=created_at,
                updated_at=updated_at,
                updated_by=updated_by,
            )
        )
        await session.flush()
        raw_asset_ids = snapshot.get("assetIds")
        for asset_id in raw_asset_ids if isinstance(raw_asset_ids, list) else []:
            session.add(
                AssetRawMaterial(
                    asset_id=UUID(str(asset_id)),
                    raw_material_id=row_id,
                    created_at=created_at,
                    updated_at=updated_at,
                    updated_by=updated_by,
                )
            )
        return
    message = f"Неизвестная таблица «{target_table}»."
    raise CellValidationError(message)


def _snapshot_datetime(value: object) -> datetime:
    """Parse a journaled ISO timestamp; the current time when it is unusable."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


async def _arms_by_id(session: AsyncSession, arm_ids: set[UUID]) -> dict[UUID, AssetRawMaterial]:
    """Batch asset-link lookup (access checks when editing form cells)."""
    if not arm_ids:
        return {}
    rows = (await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.id).in_(arm_ids)))).all()
    return {arm.id: arm for arm in rows}


async def _prices_by_arm(session: AsyncSession, arm_ids: set[UUID]) -> dict[UUID, RawMaterialPrice]:
    """Batch price lookup by link id (a link has at most one price row)."""
    if not arm_ids:
        return {}
    rows = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id).in_(arm_ids)))
    ).all()
    return {price.asset_raw_material_id: price for price in rows}


async def _movement_price(session: AsyncSession, movement: MaterialMovement) -> RawMaterialPrice:
    price = (
        await session.exec(
            select(RawMaterialPrice).where(
                col(RawMaterialPrice.asset_raw_material_id) == movement.asset_raw_material_id
            )
        )
    ).one_or_none()
    if price is None:
        raise _CELL_MOVEMENT_NO_PRICE
    return price


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


def coerce_number_or_text(value: object, field: str) -> float | str | None:
    """Coerce a number from a cell ('' / None / '1 234,5' → number or text)."""
    if field == "deviationReason":
        return coerce_text(value)
    return coerce_number(value)


def coerce_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # Any Unicode whitespace works as a thousands separator here (regular
    # space, NBSP \u00a0, narrow NBSP \u202f — Excel uses different ones).
    text = "".join(str(value).split()).replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        message = f"Не число: {value!r}"
        raise CellValidationError(message) from error


def coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def resolve_basis_id(session: AsyncSession, name: str | None) -> UUID | None:
    """Delivery basis by name (''/None → no basis)."""
    if not name:
        return None
    basis = (await session.exec(select(DeliveryBasis).where(col(DeliveryBasis.name) == name))).first()
    if basis is None:
        message = f"Базис поставки «{name}» не найден."
        raise CellValidationError(message)
    return basis.id


# ─── Write: dictionaries ─────────────────────────────────────────────


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
        updated_by=user.name or user.email or "",
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
    raw_material.updated_by = user.name or user.email or ""
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
        updated_by=user.name or user.email or "",
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
    asset.updated_by = user.name or user.email or ""
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
        updated_by=user.name or user.email or "",
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
    basis.updated_by = user.name or user.email or ""
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
