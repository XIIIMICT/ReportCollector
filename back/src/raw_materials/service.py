"""
Сервис чтения данных проекта «Сырьё» (read-only).

Запросы повторяют логику Tauri-приложения (domain.rs): строки форм
«Движение» и «Цены» с сортировкой по наименованию сырья и активу.
"""

from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import select as sqlalchemy_select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import Select

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.schemas import AssetAccessRead
from src.auth.service import CurrentUserIdentity, get_user_by_identity
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

# SQLModel-овские перегрузки select() поддерживают до 4 сущностей; для
# 5-сущностных запросов форм используем типизированный cast поверх SQLAlchemy.
MovementRowsSelect = Select[tuple[MaterialMovement, AssetRawMaterial, RawMaterialAsset, RawMaterial, RawMaterialPrice]]
PriceRowsSelect = Select[tuple[RawMaterialPrice, AssetRawMaterial, RawMaterialAsset, RawMaterial, DeliveryBasis]]


class NoRawMaterialsAccessError(Exception):
    """У пользователя нет доступа к проекту «Сырьё»."""


async def resolve_raw_materials_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
    *,
    project_key: str = RAW_MATERIALS_PROJECT_KEY,
) -> tuple[User, list[ProjectRole], list[RawMaterialAsset]]:
    """
    Возвращает (пользователь, роли, доступные активы) проекта.

    project_key позволяет проверять доступ к другому проекту той же
    механики (например, для комнат совместного редактирования).
    Администратор проекта видит все активы; работник по активам — только
    привязанные к нему. Бросает NoRawMaterialsAccessError, если у пользователя
    нет роли в проекте.
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
    """Строки формы «Движение» за отчётный месяц (как read_current_movements в Tauri)."""
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
                storageStandardTons=movement.storage_standard_tons,
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
    """Строки архивной версии «Движения» в виде MovementRowRead для выгрузки."""
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
    """Строки формы «Цены» (как read_current_prices в Tauri)."""
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
    """Строки справочника «Сырьевые позиции» с активами."""
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
    """Строки справочника «Активы» в пределах доступа пользователя."""
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
    """Строки справочника «Базисы поставки»."""
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
    Строки справочника «Пользователи» (проект «Сырьё», без удалённых).

    Показываются все пользователи, у которых есть связь с проектом (включая
    неактивных — их активирует администратор проекта). isActive и isAdmin
    берутся из пер-проектной связи, а не из глобальных полей пользователя.
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
    Аудит строки «Движения»: берётся более свежая правка из движения или цен.

    В SQLite это CASE-сравнение строк; в Postgres business_plan_updated_at —
    TEXT (может содержать заглушку '<УГАДАЙТЕ>'), поэтому невалидные значения
    трактуются как «не обновлялось» и выбирается аудит движения.
    """
    plan_updated_at = parse_datetime_or_none(price.business_plan_updated_at)
    if plan_updated_at is not None and plan_updated_at > movement.updated_at:
        return plan_updated_at, price.business_plan_updated_by
    return movement.updated_at, movement.updated_by


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


# ─── Запись: правки ячеек форм «Движение» и «Цены» ────────────────

# Поля формы «Движение», которые пишутся в MaterialMovement.
# Типовые сообщения об ошибках записи (модульные константы — стиль проекта).
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
    "storageStandardTons": "storage_standard_tons",
    "deviationReason": "deviation_reason",
}

# Поля формы «Движение», которые пишутся в RawMaterialPrice (бизнес-план).
MOVEMENT_PRICE_FIELDS: dict[str, str] = {
    "businessPlanTons": "business_plan_quantity_tons",
}

# Поля формы «Цены», которые пишутся в RawMaterialPrice.
PRICE_CELL_FIELDS: dict[str, str] = {
    "fact2025": "fact_2025",
    "businessPlanQuantityTons": "business_plan_quantity_tons",
    "businessPlanUsdPerTon": "business_plan_usd_per_ton",
    "businessPlanRubPerTon": "business_plan_rub_per_ton",
    "deliveryCostRub": "delivery_cost_rub",
}


class CellValidationError(ValueError):
    """Невалидная ячейка (имя поля или значение)."""


async def update_movement_cells(
    session: AsyncSession,
    *,
    report_month: date,
    updates: list[CellUpdate],
    user: User,
    assets: list[RawMaterialAsset],
) -> list[AppliedCell]:
    """Применяет правки ячеек формы «Движение» и пишет журнал (в той же транзакции)."""
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        movement = await _movement_row(session, row_id, report_month, asset_ids)
        if field in MOVEMENT_CELL_FIELDS:
            column = MOVEMENT_CELL_FIELDS[field]
            old_value = getattr(movement, column)
            new_value = coerce_number_or_text(value, field)
            if new_value == old_value:
                continue
            setattr(movement, column, new_value)
        elif field in MOVEMENT_PRICE_FIELDS:
            price = await _movement_price(session, movement)
            column = MOVEMENT_PRICE_FIELDS[field]
            old_value = getattr(price, column)
            new_value = coerce_number_or_text(value, field)
            if new_value == old_value:
                continue
            setattr(price, column, new_value)
            price.business_plan_updated_at = datetime.now(UTC).isoformat()
            price.business_plan_updated_by = user.name or user.email or ""
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
) -> list[AppliedCell]:
    """Применяет правки ячеек формы «Цены» и пишет журнал (в той же транзакции)."""
    asset_ids = [asset.id for asset in assets]
    applied: list[AppliedCell] = []
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row_id = update.rowId
        field = update.field
        value = update.value
        price = await _price_row(session, row_id, asset_ids)
        if field == "deliveryBasisName":
            old_value = await _basis_name(session, price.delivery_basis_id)
            new_value = coerce_text(value)
            price.delivery_basis_id = await resolve_basis_id(session, new_value)
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
    """Применяет нормализованное значение ячейки (undo/redo) с обновлением аудита."""
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    if target_table == MOVEMENT_TABLE:
        movement = await session.get(MaterialMovement, row_id)
        if movement is None:
            raise _CELL_MOVEMENT_NOT_FOUND
        if field in MOVEMENT_PRICE_FIELDS:
            price = await _movement_price(session, movement)
            setattr(price, MOVEMENT_PRICE_FIELDS[field], value)
            price.business_plan_updated_at = now.isoformat()
            price.business_plan_updated_by = updated_by
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
    # Справочники: undo/redo правок имён и наборов активов.
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
                user_row.key = new_email
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


async def _basis_name(session: AsyncSession, basis_id: UUID | None) -> str | None:
    """Имя базиса поставки по id (для журнала ячейки deliveryBasisName)."""
    if basis_id is None:
        return None
    basis = await session.get(DeliveryBasis, basis_id)
    return basis.name if basis is not None else None


async def _movement_row(
    session: AsyncSession,
    row_id: UUID,
    report_month: date,
    asset_ids: list[UUID],
) -> MaterialMovement:
    movement = await session.get(MaterialMovement, row_id)
    if movement is None or movement.report_month != report_month:
        raise _CELL_MOVEMENT_NOT_FOUND
    if asset_ids and not await _arm_belongs_to_assets(session, movement.asset_raw_material_id, asset_ids):
        raise _CELL_MOVEMENT_NO_ACCESS
    return movement


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


async def _price_row(session: AsyncSession, row_id: UUID, asset_ids: list[UUID]) -> RawMaterialPrice:
    price = await session.get(RawMaterialPrice, row_id)
    if price is None:
        raise _CELL_PRICE_NOT_FOUND
    if asset_ids and not await _arm_belongs_to_assets(session, price.asset_raw_material_id, asset_ids):
        raise _CELL_PRICE_NO_ACCESS
    return price


async def _arm_belongs_to_assets(session: AsyncSession, arm_id: UUID, asset_ids: list[UUID]) -> bool:
    """Связка актив-сырьё относится к одному из активов пользователя."""
    arm = await session.get(AssetRawMaterial, arm_id)
    return arm is not None and arm.asset_id in asset_ids


def coerce_number_or_text(value: object, field: str) -> float | str | None:
    """Число из ячейки ('' / None / '1 234,5' → число или текст)."""
    if field == "deviationReason":
        return coerce_text(value)
    return coerce_number(value)


def coerce_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
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
    """Базис поставки по имени (''/None → без базиса)."""
    if not name:
        return None
    basis = (await session.exec(select(DeliveryBasis).where(col(DeliveryBasis.name) == name))).first()
    if basis is None:
        message = f"Базис поставки «{name}» не найден."
        raise CellValidationError(message)
    return basis.id


# ─── Запись: справочники ──────────────────────────────────────────


class DictionaryReferenceError(ValueError):
    """Нарушение ссылочной целостности при изменении справочника."""


_DICT_REF_USER_MANAGED_BY_KEYCLOAK = DictionaryReferenceError(
    "Нельзя изменить логин и ФИО: учётные записи управляются Keycloak."
)
_DICT_REF_USER_DELETE_FORBIDDEN = DictionaryReferenceError(
    "Удаление пользователей недоступно. Чтобы ограничить доступ, отключите флаг «Активен»."
)


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
    """Идентификаторы активов по именам (для отката assetNames при undo/redo)."""
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
    for link in links:
        if await _arm_is_used(session, link.id):
            raise _DICT_REF_RAW_MATERIAL_USED
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
    for link in links:
        if await _arm_is_used(session, link.id):
            raise _DICT_REF_ASSET_USED
        await session.delete(link)
    user_links = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.asset_id) == asset_id))
    ).all()
    for link in user_links:
        await session.delete(link)
    await session.delete(asset)
    await session.commit()


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
    await session.commit()


async def create_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    is_admin: bool,
    asset_names: list[str],
) -> User:
    """Создаёт локального пользователя проекта «Сырьё» с ролью и активами."""
    email = email.strip()
    existing = (await session.exec(select(User).where(col(User.email) == email))).one_or_none()
    if existing is not None:
        message = f"Пользователь с email {email} уже существует."
        raise DictionaryReferenceError(message)
    now = datetime.now(UTC)
    user = User(
        key=email,
        email=email,
        name=fullname.strip(),
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
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
        # При включённом Keycloak логин и ФИО приходят из идентичности при каждом
        # входе (upsert_current_user), поэтому редактирование здесь запрещено.
        if email is not None and email.strip() != user.email:
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
        if fullname is not None and fullname.strip() != (user.name or ""):
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
    old_email = user.email
    old_fullname = user.name
    old_is_admin = link.role == ProjectRole.ADMIN
    old_is_active = link.is_active
    old_asset_names = await _user_asset_names(session, user.id)
    new_email = email.strip() if email is not None else old_email
    new_fullname = fullname.strip() if fullname is not None else old_fullname
    new_is_admin = is_admin if is_admin is not None else old_is_admin
    new_is_active = is_active if is_active is not None else old_is_active
    new_asset_names = asset_names if asset_names is not None else ([] if is_admin is not None else old_asset_names)
    if email is not None:
        candidate = email.strip()
        other = (await session.exec(select(User).where(col(User.email) == candidate))).one_or_none()
        if other is not None and other.id != user.id:
            message = f"Пользователь с email {candidate} уже существует."
            raise DictionaryReferenceError(message)
        user.email = candidate
        user.key = candidate
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
    session: AsyncSession,  # noqa: ARG001 — контракт вызова
    *,
    project_id: UUID,  # noqa: ARG001 — контракт вызова
    user_id: UUID,  # noqa: ARG001 — контракт вызова
) -> None:
    """
    Удаление пользователей из проекта запрещено.

    Вместо удаления администратор отключает доступ флагом «Активен»
    (is_active=False): учётная запись и связи с проектами сохраняются,
    а доступ к проекту «Сырьё» закрывается.
    """
    raise _DICT_REF_USER_DELETE_FORBIDDEN


async def replace_asset_links(session: AsyncSession, raw_material_id: UUID, asset_ids: list[UUID]) -> None:
    """Заменяет связки актив-сырьё для сырьевой позиции (по разнице)."""
    existing = {
        link.asset_id: link
        for link in (
            await session.exec(select(AssetRawMaterial).where(col(AssetRawMaterial.raw_material_id) == raw_material_id))
        ).all()
    }
    existing_ids = set(existing)
    new_ids = set(asset_ids)
    # Удаляем только снятые активы. Связи с движением/ценами защищены
    # RESTRICT-ключом — это бизнес-правило, а не ошибка вставки.
    for asset_id in existing_ids - new_ids:
        await session.delete(existing[asset_id])
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        names = await _asset_names_by_ids(session, sorted(existing_ids - new_ids))
        raise DictionaryReferenceError(
            "Нельзя убрать активы, по которым есть данные движения: " + ", ".join(names)
        ) from error
    now = datetime.now(UTC)
    for asset_id in asset_ids:
        if asset_id not in existing_ids:
            session.add(
                AssetRawMaterial(
                    asset_id=asset_id,
                    raw_material_id=raw_material_id,
                    created_at=now,
                    updated_at=now,
                )
            )
    await session.flush()


async def replace_user_assets(session: AsyncSession, user_id: UUID, asset_names: list[str] | None) -> None:
    """Заменяет привязку пользователя к активам (None — только удалить связи админа)."""
    existing = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.user_id) == user_id))
    ).all()
    for link in existing:
        await session.delete(link)
    # Удаления применяем до вставок: у связей уникальный ключ (user_id, asset_id).
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


async def _arm_is_used(session: AsyncSession, arm_id: UUID) -> bool:
    movement = (
        await session.exec(select(MaterialMovement).where(col(MaterialMovement.asset_raw_material_id) == arm_id))
    ).first()
    if movement is not None:
        return True
    price = (
        await session.exec(select(RawMaterialPrice).where(col(RawMaterialPrice.asset_raw_material_id) == arm_id))
    ).first()
    return price is not None
