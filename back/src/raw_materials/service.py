"""
Read service for the «Сырьё» (raw materials) project (read-only).

Queries mirror the Tauri app logic (domain.rs): «Движение» and «Цены»
form rows sorted by raw-material name and asset.
"""

from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import select as sqlalchemy_select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import Select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.emails import is_valid_email, normalize_email
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.schemas import AssetAccessRead
from src.auth.service import CurrentUserIdentity, get_user_by_identity, sync_project_memberships
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.models import MOVEMENT_TABLE, PRICE_TABLE
from src.collab.store import get_collab_store
from src.config import get_settings
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
    UserRowRead,
)

# SQLModel's select() overloads support up to 4 entities; for the 5-entity
# form queries we use a typed cast over SQLAlchemy.
MovementRowsSelect = Select[tuple[MaterialMovement, AssetRawMaterial, RawMaterialAsset, RawMaterial, RawMaterialPrice]]
PriceRowsSelect = Select[tuple[RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis]]


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
    if assets:
        asset_ids = [asset.id for asset in assets]
        query = query.where(col(RawMaterialAsset.id).in_(asset_ids))

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
) -> tuple[MovementSnapshotRead | None, list[MovementRowRead]]:
    """Archive «Движение» version rows as MovementRowRead for export."""
    snapshot = await session.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        return None, []
    rows = (
        await session.exec(
            select(SnapshotMaterialMovement)
            .where(col(SnapshotMaterialMovement.snapshot_id) == snapshot_id)
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
    if assets:
        asset_ids = [asset.id for asset in assets]
        query = query.where(col(RawMaterialAsset.id).in_(asset_ids))

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


async def read_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
) -> list[UserRowRead]:
    """
    «Пользователи» dictionary rows (the «Сырьё» project, without deleted).

    Shows all users who have a link with the project (including inactive
    ones — the project admin activates them). isActive and isAdmin come
    from the per-project link, not from the user's global fields.
    """
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).first()
    if project_id is None:
        return []
    query = (
        select(User, UserRole)
        .join(UserRole, col(UserRole.user_id) == col(User.id))
        .where(col(UserRole.project_id) == project_id, col(User.is_deleted) == False)  # noqa: E712
        .order_by(col(UserRole.is_active).desc(), func.coalesce(User.name, ""))
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    rows = (await session.exec(query)).all()
    users = [user for user, _ in rows]
    links_by_user = {user.id: link for user, link in rows}
    asset_links = (
        await session.exec(
            select(RawMaterialUserAsset, RawMaterialAsset)
            .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(RawMaterialUserAsset.asset_id))
            .where(col(RawMaterialUserAsset.user_id).in_([user.id for user in users]))
            .order_by(RawMaterialAsset.name)
        )
    ).all()
    names_by_user: dict[UUID, list[str]] = {}
    for link, asset in asset_links:
        names_by_user.setdefault(link.user_id, []).append(asset.name)
    return [
        UserRowRead(
            id=user.id,
            email=user.email,
            fullname=user.name or "",
            isAdmin=link.role == ProjectRole.ADMIN,
            assetNames=names_by_user.get(user.id, []),
            isActive=link.is_active,
            lastAuthenticatedAt=user.last_authenticated_at,
            createdAt=user.created_at,
            updatedAt=link.updated_at,
            updatedBy=link.updated_by,
        )
        for user, link in ((user, links_by_user[user.id]) for user in users)
    ]


def movement_audit(
    movement: MaterialMovement,
    price: RawMaterialPrice,
) -> tuple[datetime, str]:
    """
    «Движение» row audit: takes the freshest edit from movement or prices.

    In SQLite this is a CASE string comparison; in Postgres
    business_plan_updated_at is TEXT (may hold the '<УГАДАЙТЕ>' stub), so
    invalid values are treated as "not updated" and the movement audit wins.
    """
    candidates: list[tuple[datetime, str]] = [(movement.updated_at, movement.updated_by)]
    plan_updated_at = parse_datetime_or_none(price.business_plan_updated_at)
    if plan_updated_at is not None:
        candidates.append((plan_updated_at, price.business_plan_updated_by))
    storage_updated_at = parse_datetime_or_none(price.storage_standard_updated_at)
    if storage_updated_at is not None:
        candidates.append((storage_updated_at, price.storage_standard_updated_by))
    return max(candidates, key=lambda c: c[0])


def parse_datetime_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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
_DICT_REF_USER_NOT_FOUND = ValueError("Пользователь не найден.")
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


class CellValidationError(ValueError):
    """Invalid cell (field name or value)."""


_DICT_CREATED_SNAPSHOT_INVALID = CellValidationError("Некорректный снимок записи.")


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
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    # Batch-load rows, links and prices up front: one query per table instead
    # of two-three per cell (a paste can carry hundreds of cells).
    row_ids = [update.rowId for update in updates if update.rowId is not None]
    movements = {
        movement.id: movement
        for movement in (
            await session.exec(select(MaterialMovement).where(col(MaterialMovement.id).in_(row_ids)))
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
        if asset_ids and (arm is None or arm.asset_id not in asset_ids):
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
                price.business_plan_updated_at = datetime.now(UTC).isoformat()
                price.business_plan_updated_by = user.name or user.email or ""
            elif field == "storageStandardTons":
                price.storage_standard_updated_at = datetime.now(UTC).isoformat()
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
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="materialMovement",
        report_month=report_month,
        session_id=session_id,
        applied=applied,
    )
    await session.commit()
    return applied


async def update_price_cells(
    session: AsyncSession,
    *,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
    session_id: str,
) -> list[AppliedCell]:
    """Apply «Цены» form cell edits and write the journal (same transaction)."""
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    # Batch-load prices and links up front (one query per table); the basis
    # dictionary is tiny, so it's loaded whole once for journal old-values.
    row_ids = [update.rowId for update in updates if update.rowId is not None]
    prices = {
        price.id: price
        for price in (await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.id).in_(row_ids)))).all()
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
        if asset_ids and (arm is None or arm.asset_id not in asset_ids):
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
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RAW_MATERIALS_PROJECT_KEY,
        section="rawMaterialPrices",
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await session.commit()
    return applied


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
    if target_table == MOVEMENT_TABLE:
        movement = await session.get(MaterialMovement, row_id)
        if movement is None:
            raise _CELL_MOVEMENT_NOT_FOUND
        if field in MOVEMENT_PRICE_FIELDS:
            price = await _movement_price(session, movement)
            setattr(price, MOVEMENT_PRICE_FIELDS[field], value)
            if field == "businessPlanTons":
                price.business_plan_updated_at = now.isoformat()
                price.business_plan_updated_by = updated_by
            elif field == "storageStandardTons":
                price.storage_standard_updated_at = now.isoformat()
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
                raise _DICT_REF_USER_NOT_FOUND
            link.is_active = False
            link.updated_at = datetime.now(UTC)
            session.add(link)
            await replace_user_assets(session, row_id, [])
            await session.commit()
            return
        if not isinstance(snapshot, dict):
            raise _DICT_CREATED_SNAPSHOT_INVALID
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
        await session.commit()
        return
    if snapshot is None:
        if target_table == RawMaterialAsset.__tablename__:
            await delete_asset(session, asset_id=row_id)
            return
        if target_table == DeliveryBasis.__tablename__:
            await delete_delivery_basis(session, basis_id=row_id)
            return
        if target_table == RawMaterial.__tablename__:
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
        session.add(RawMaterialAsset(id=row_id, name=str(name), created_at=now, updated_at=now, updated_by=updated_by))
        return
    if target_table == DeliveryBasis.__tablename__:
        session.add(DeliveryBasis(id=row_id, name=str(name), created_at=now, updated_at=now, updated_by=updated_by))
        return
    if target_table == RawMaterial.__tablename__:
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


class DictionaryReferenceError(ValueError):
    """Referential-integrity violation when changing a dictionary."""


_DICT_REF_USER_MANAGED_BY_KEYCLOAK = DictionaryReferenceError(
    "Нельзя изменить email и ФИО: учётные записи управляются Keycloak."
)
_DICT_REF_USER_DELETE_FORBIDDEN = DictionaryReferenceError(
    "Удаление пользователей недоступно. Чтобы ограничить доступ, отключите флаг «Активен»."
)
_DICT_REF_USER_BAD_EMAIL = DictionaryReferenceError("Некорректный email.")


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
    await session.commit()
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


async def _user_asset_names(session: AsyncSession, user_id: UUID) -> list[str]:
    rows = (
        await session.exec(
            select(RawMaterialAsset.name)
            .join(RawMaterialUserAsset, col(RawMaterialUserAsset.asset_id) == col(RawMaterialAsset.id))
            .where(RawMaterialUserAsset.user_id == user_id)
            .order_by(RawMaterialAsset.name)
        )
    ).all()
    return list(rows)


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
    await session.commit()
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


async def delete_raw_material(session: AsyncSession, *, raw_material_id: UUID) -> None:
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
    await session.delete(raw_material)
    await session.commit()


async def create_asset(session: AsyncSession, *, name: str, user: User) -> RawMaterialAsset:
    asset = RawMaterialAsset(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=user.name or user.email or "",
    )
    session.add(asset)
    await session.commit()
    return asset


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
    await session.commit()
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


async def delete_asset(session: AsyncSession, *, asset_id: UUID) -> None:
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
    for link in user_links:
        await session.delete(link)
    await session.delete(asset)
    try:
        await session.commit()
    except IntegrityError as error:
        # A reference slipped in between the reference check and the commit
        # (a race) — surface a clean 409, never a 500.
        await session.rollback()
        raise _DICT_REF_ASSET_USED from error


async def create_delivery_basis(session: AsyncSession, *, name: str, user: User) -> DeliveryBasis:
    basis = DeliveryBasis(
        name=name.strip(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=user.name or user.email or "",
    )
    session.add(basis)
    await session.commit()
    return basis


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
    await session.commit()
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


async def delete_delivery_basis(session: AsyncSession, *, basis_id: UUID) -> None:
    basis = await session.get(DeliveryBasis, basis_id)
    if basis is None:
        raise _DICT_REF_BASIS_NOT_FOUND
    price = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.delivery_basis_id) == basis_id))
    ).first()
    if price is not None:
        raise _DICT_REF_BASIS_USED
    await session.delete(basis)
    try:
        await session.commit()
    except IntegrityError as error:
        # A reference slipped in between the check and the commit (a race), or a
        # table we don't pre-check references the basis — surface a clean 409,
        # never a 500.
        await session.rollback()
        raise _DICT_REF_BASIS_USED from error


async def create_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    is_admin: bool,
    asset_names: list[str],
) -> User:
    """
    Add a user to the «Сырьё» project with role and assets.

    The user is immediately provisioned into all projects: active with the
    chosen role in the current one, inactive with the asset-worker role in
    the others (the admin opens project access with the «Активен» flag). If
    a user with this email already exists (e.g. created by another admin or
    logged in via Keycloak) but has no link with the current project — only
    the project link is created; the user's existing data is not
    overwritten (FIO/email come from the identity after a Keycloak login).
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise _DICT_REF_USER_BAD_EMAIL
    # Case-insensitive lookup: addresses are case-insensitive and the DB may
    # hold records created before normalization.
    existing = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
    if existing is not None:
        link = (
            await session.exec(
                select(UserRole).where(UserRole.user_id == existing.id, UserRole.project_id == project_id)
            )
        ).one_or_none()
        if link is not None:
            message = f"Пользователь с email {email} уже существует."
            raise DictionaryReferenceError(message)
        user = existing
    else:
        now = datetime.now(UTC)
        user = User(
            email=email,
            name=fullname.strip(),
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
    role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    now = datetime.now(UTC)
    session.add(
        UserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    await replace_user_assets(session, user.id, asset_names)
    # Provisioning into all other projects: new links are inactive with the
    # asset-worker role; existing links are left untouched.
    await sync_project_memberships(session, user.id)
    await session.commit()
    return user


async def update_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    email: str | None,
    fullname: str | None,
    is_admin: bool | None,
    asset_names: list[str] | None,
    is_active: bool | None,
    reset_password: bool,
    updated_by: str = "",
) -> tuple[User, list[AppliedCell]]:
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_REF_USER_NOT_FOUND
    link = (
        await session.exec(select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_REF_USER_NOT_FOUND
    if get_settings().keycloak_enabled:
        # With Keycloak enabled, email and FIO come from the identity on every
        # login (upsert_current_user), so editing them here is forbidden.
        if email is not None and normalize_email(email) != normalize_email(user.email or ""):
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
        if fullname is not None and fullname.strip() != (user.name or ""):
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
    old_email = user.email
    old_fullname = user.name
    old_is_admin = link.role == ProjectRole.ADMIN
    old_is_active = link.is_active
    old_asset_names = await _user_asset_names(session, user.id)
    new_email = normalize_email(email) if email is not None else old_email
    new_fullname = fullname.strip() if fullname is not None else old_fullname
    new_is_admin = is_admin if is_admin is not None else old_is_admin
    new_is_active = is_active if is_active is not None else old_is_active
    new_asset_names = asset_names if asset_names is not None else ([] if is_admin is not None else old_asset_names)
    if email is not None:
        candidate = normalize_email(email)
        if not is_valid_email(candidate):
            raise _DICT_REF_USER_BAD_EMAIL
        other = (await session.exec(select(User).where(func.lower(col(User.email)) == candidate))).one_or_none()
        if other is not None and other.id != user.id:
            message = f"Пользователь с email {candidate} уже существует."
            raise DictionaryReferenceError(message)
        user.email = candidate
    if fullname is not None:
        user.name = fullname.strip()
    if is_active is not None:
        link.is_active = is_active
    if reset_password:
        user.password_hash = None
    role_changed = is_admin is not None and (link.role == ProjectRole.ADMIN) != is_admin
    if is_admin is not None:
        link.role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    if asset_names is not None or role_changed:
        await replace_user_assets(
            session,
            user.id,
            asset_names if asset_names is not None else ([] if is_admin else None),
        )
    link.updated_at = datetime.now(UTC)
    link.updated_by = updated_by
    user.updated_at = datetime.now(UTC)
    session.add(user)
    session.add(link)
    await session.commit()
    applied: list[AppliedCell] = []
    if new_email != old_email:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="email",
                old_value=old_email,
                new_value=new_email,
            )
        )
    if new_fullname != old_fullname:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="fullname",
                old_value=old_fullname,
                new_value=new_fullname,
            )
        )
    if new_is_admin != old_is_admin:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="isAdmin",
                old_value=old_is_admin,
                new_value=new_is_admin,
            )
        )
    if new_is_active != old_is_active:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="isActive",
                old_value=old_is_active,
                new_value=new_is_active,
            )
        )
    if new_asset_names != old_asset_names:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="assetNames",
                old_value=old_asset_names,
                new_value=new_asset_names,
            )
        )
    return user, applied


async def delete_project_user(
    session: AsyncSession,  # noqa: ARG001 — call contract
    *,
    project_id: UUID,  # noqa: ARG001 — call contract
    user_id: UUID,  # noqa: ARG001 — call contract
) -> None:
    """
    Reject user deletion from the project.

    Instead of deleting, the admin disables access with the «Активен» flag
    (is_active=False): the account and project links stay, only the access
    to the «Сырьё» project is closed.
    """
    raise _DICT_REF_USER_DELETE_FORBIDDEN


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


async def replace_user_assets(session: AsyncSession, user_id: UUID, asset_names: list[str] | None) -> None:
    """Replace the user's asset assignment (None — only remove admin links)."""
    existing = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.user_id) == user_id))
    ).all()
    for link in existing:
        await session.delete(link)
    # Deletions flush before inserts: the links have a unique key (user_id, asset_id).
    await session.flush()
    if asset_names:
        assets = (await session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).in_(asset_names)))).all()
        names_found = {asset.name for asset in assets}
        missing = [name for name in asset_names if name not in names_found]
        if missing:
            message = f"Активы не найдены: {', '.join(missing)}"
            raise DictionaryReferenceError(message)
        now = datetime.now(UTC)
        for asset in assets:
            session.add(RawMaterialUserAsset(user_id=user_id, asset_id=asset.id, created_at=now))
    await session.flush()


async def _delete_arm_rows(session: AsyncSession, arm_id: UUID) -> None:
    """Delete the link's (empty) «Движение» and «Цены» rows before deleting the link itself."""
    await session.exec(delete(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id) == arm_id))
    await session.exec(delete(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id) == arm_id))
